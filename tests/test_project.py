"""Offline integration tests. Model inference is replaced at the process boundary."""

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import codex_worker
import config as router_config
import native_handoff
import router
import setup_router


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "科研 项目"
        self.folder.mkdir()
        self.brief = self.folder / "任务 说明.md"
        self.content = "验证模型差异。\nLiteral: $() & |"
        self.brief.write_text(self.content, encoding="utf-8-sig")
        self.run_flags = ["--workspace", str(self.folder), "--brief", str(self.brief), "--run"]
        self.config = self.folder / "测试设置.json"
        self.config.write_text('{"schema_version":1}', encoding="utf-8")
        # No external program can execute from a route under test.
        block_processes = patch.object(subprocess, "Popen", side_effect=AssertionError("unexpected child"))
        block_processes.start()
        self.addCleanup(block_processes.stop)
        worker = patch.object(router, "run_codex", spec=True, return_value="已复核")
        self.worker = worker.start()
        self.addCleanup(worker.stop)

    def cli(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        # Ignore the developer's own machine-local profile while testing defaults.
        argv = ["router.py", *args, "--config", str(self.config)]
        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = router.main()
            except SystemExit as exc:
                code = exc.code
        return code, stdout.getvalue(), stderr.getvalue()

    def success(self, *args):
        code, stdout, stderr = self.cli(*args)
        self.assertEqual(code, 0, stderr)
        return json.loads(stdout)

    def test_preview_selects_one_worker_without_inference(self):
        for kind, worker in [("chat", "neon"), ("design", "carbon"), ("implement", "gpt"), ("review", "claude")]:
            with self.subTest(kind=kind):
                plan = self.success(kind)
                self.assertEqual((plan["status"], plan["worker"]), ("preview", worker))
                self.assertIsNone(plan["escalation_reason"])
                self.assertIn("medium", plan["effort"].lower())
        self.worker.assert_not_called()

    def test_pool_filtering_preserves_readonly_review(self):
        plan = self.success("review", "--avoid-pool", "claude", "--avoid-pool", "gemini")
        self.assertEqual((plan["worker"], plan["access"]), ("gpt", "read-only"))
        for args in [
            ("chat", "--avoid-pool", "gemini"),
            ("review", "--worker", "claude", "--avoid-pool", "claude"),
            ("implement", "--worker", "carbon"),
            ("review", "--avoid-pool", "unregistered"),
        ]:
            with self.subTest(args=args):
                self.assertNotEqual(self.cli(*args)[0], 0)
        self.worker.assert_not_called()

    def test_escalation_requires_a_nonempty_reason(self):
        for flag in ("--failed-check", "--hard-reason"):
            with self.subTest(flag=flag):
                plan = self.success("implement", flag, "同一样本两次复算相矛盾")
                self.assertEqual(plan["escalation_reason"], "同一样本两次复算相矛盾")
                self.assertNotEqual(plan["effort"], "medium")
                self.assertNotEqual(self.cli("implement", flag, "   ")[0], 0)
        self.worker.assert_not_called()

    def test_native_run_returns_handoff_without_inference(self):
        for kind in ("chat", "design", "review"):
            with self.subTest(kind=kind):
                result = self.success(kind, *self.run_flags)
                self.assertEqual(result["status"], "handoff_ready")
                self.assertEqual(result["brief"], self.content)
                self.assertEqual(Path(result["workspace"]), self.folder)
                self.assertNotIn("reply", result)
        self.worker.assert_not_called()

    def test_gpt_implementation_and_review_dispatch_once_each(self):
        for args in [("implement",), ("review", "--worker", "gpt")]:
            with self.subTest(args=args):
                self.worker.reset_mock()
                result = self.success(*args, *self.run_flags)
                self.assertEqual((result["status"], result["reply"]), ("worker_replied", "已复核"))
                self.worker.assert_called_once()
                prompt, = self.worker.call_args.args
                self.assertIn(self.content, prompt)
                self.assertIn(str(self.folder), prompt)
                self.assertEqual(self.worker.call_args.kwargs["write"], args[0] == "implement")

    def test_invalid_or_incomplete_live_args_never_dispatch(self):
        for args in [
            ["--run"], ["--brief", str(self.brief), "--run"],
            ["--workspace", str(self.folder), "--run"],
            ["--brief", str(self.brief), "--workspace", str(self.brief), "--run"],
            ["--brief", str(self.folder / "missing"), "--workspace", str(self.folder), "--run"],
        ]:
            with self.subTest(args=args):
                self.assertNotEqual(self.cli("implement", *args)[0], 0)
        for data in (b" \n ", b"\xff\xfe"):
            self.brief.write_bytes(data)
            self.assertNotEqual(self.cli("implement", *self.run_flags)[0], 0)
        self.worker.assert_not_called()

    def test_failed_worker_does_not_return_success_or_retry(self):
        self.worker.side_effect = codex_worker.CodexWorkerError("模拟额度限制")
        code, stdout, stderr = self.cli("implement", *self.run_flags)
        self.assertNotEqual(code, 0)
        self.assertEqual(stdout.strip(), "")
        self.assertIn("模拟额度限制", json.loads(stderr)["message"])
        self.worker.assert_called_once()

    def test_config_overrides_only_supplied_profiles_and_disables_workers(self):
        config = {
            "schema_version": 1,
            "disabled_workers": ["carbon"],
            "profiles": {"gpt": {
                "standard": {"model": "gpt-test", "effort": "medium"},
                "hard": {"model": "gpt-best", "effort": "max"},
            }},
        }
        self.config.write_text(json.dumps(config), encoding="utf-8")
        normal = self.success("design")
        self.assertEqual((normal["worker"], normal["model"], normal["effort"]), ("gpt", "gpt-test", "medium"))
        hard = self.success("implement", "--hard-reason", "证明中的两个条件互相矛盾")
        self.assertEqual((hard["model"], hard["effort"]), ("gpt-best", "max"))
        self.assertEqual(self.success("review")["worker"], "claude")
        self.assertNotEqual(self.cli("design", "--worker", "carbon")[0], 0)
        self.worker.assert_not_called()

    def test_roundtrip_preview_written_as_utf8_json(self):
        artifact = self.folder / "路由 记录.json"
        result = self.success("implement", "--hard-reason", "两次验收结果不一致", "--output", str(artifact))
        self.assertEqual(json.loads(artifact.read_text(encoding="utf-8")), result)
        self.worker.assert_not_called()

    def test_existing_output_is_preserved_before_inference(self):
        output = self.folder / "已有记录.json"
        output.write_bytes(b"first run")
        self.assertNotEqual(self.cli("implement", *self.run_flags, "--output", str(output))[0], 0)
        self.assertEqual(output.read_bytes(), b"first run")
        self.worker.assert_not_called()

    def test_invalid_config_cannot_dispatch(self):
        for data in ['{"schema_version": 1, "codex_pasth": "wrong"}', '{malformed']:
            self.config.write_text(data, encoding="utf-8")
            self.assertNotEqual(self.cli("implement", *self.run_flags)[0], 0)
        self.config.unlink()
        self.assertNotEqual(self.cli("implement", *self.run_flags)[0], 0)
        self.worker.assert_not_called()


class CodexWorkerTests(unittest.TestCase):
    def test_fake_process_receives_args_and_utf8_stdin(self):
        state = {
            "executable": "fake-codex", "available": True, "chatgpt_login": True,
            "missing_flags": [], "model_efforts": {}, "capabilities": sorted(codex_worker.REQUIRED_FLAGS),
        }
        prompt = "仅审查 $env:PATH & echo 测试"
        seen = {}

        def fake_process(argv, **kwargs):
            seen["argv"] = argv
            seen["cwd"] = kwargs["cwd"]
            self.assertIsInstance(argv, list)
            self.assertNotIn(prompt, argv)
            self.assertFalse(kwargs.get("shell"))
            answer = Path(argv[argv.index("--output-last-message") + 1])
            process = Mock(returncode=0)

            def communicate(data, timeout):
                self.assertEqual(data, prompt.encode("utf-8"))
                answer.write_text("确认", encoding="utf-8")

            process.communicate.side_effect = communicate
            return process

        with tempfile.TemporaryDirectory() as folder, patch.object(codex_worker, "codex_status", return_value=state), patch.object(codex_worker, "_environment", return_value={}), patch.object(subprocess, "Popen", side_effect=fake_process):
            self.assertEqual(codex_worker.run_codex(prompt, workspace=folder), "确认")
        argv = seen["argv"]
        self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
        self.assertIn('model_reasoning_effort="medium"', argv)
        self.assertEqual(Path(seen["cwd"]), Path(folder).resolve())
        self.assertEqual(argv[-1], "-")


class NativeHandoffTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.binary = self.folder / ("agy.exe" if os.name == "nt" else "agy")
        self.binary.touch()
        self.binary.chmod(0o700)
        self.slug = "gemini-4-carbon-medium"
        self.catalog = f"{self.slug} Gemini 4 Carbon (Medium)\ngemini-4-carbon-high Gemini 4 Carbon (High)\n"
        self.packet = {
            "worker": "carbon", "model": "Gemini 4 Carbon", "native_slug": self.slug,
            "effort": "medium", "workspace": str(self.folder), "access": "read-only",
            "task": "design", "brief": "独立评估两个方案。",
            "status": "handoff_ready", "delivery": "antigravity-native",
        }
        self.handoff = self.folder / "原生交接单.json"
        self.handoff.write_text(json.dumps(self.packet, ensure_ascii=False), encoding="utf-8")
        for p in [
            patch.object(Path, "home", return_value=self.folder),
            patch.dict(os.environ, {}, clear=True),
            patch.object(subprocess, "Popen", side_effect=AssertionError("unexpected external process")),
        ]:
            p.start()
            self.addCleanup(p.stop)

    def responses(self, model, result_code=0):
        events = [
            {"event": "init", "init": {"model": model}},
            {"event": "result", "result": {"status": "SUCCESS", "response": "独立建议"}},
        ]
        stream = "\n".join(json.dumps(event, ensure_ascii=False) for event in events)
        return [
            subprocess.CompletedProcess([], 0, self.catalog.encode("utf-8"), b""),
            subprocess.CompletedProcess([], result_code, stream.encode("utf-8"), b""),
        ]

    def test_mock_native_cli_roundtrip_confirms_actual_model(self):
        args = ["native_handoff.py", "--handoff", str(self.handoff), "--agy", str(self.binary),
                "--config", str(ROOT / "router.example.json")]
        stdout = io.StringIO()
        with patch.object(sys, "argv", args), patch.object(subprocess, "run", side_effect=self.responses(self.slug)) as fake, contextlib.redirect_stdout(stdout):
            self.assertEqual(native_handoff.main(), 0)
        result = json.loads(stdout.getvalue())
        self.assertEqual((result["status"], result["reply"], result["model"]), ("worker_replied", "独立建议", self.slug))
        self.assertEqual(fake.call_count, 2)
        catalog_call, worker_call = fake.call_args_list
        self.assertEqual(catalog_call.args[0][-1], "models")
        argv = worker_call.args[0]
        self.assertEqual(argv[argv.index("--model") + 1], self.slug)
        self.assertNotIn(self.packet["brief"], argv)
        request = json.loads(worker_call.kwargs["input"].decode("utf-8"))
        self.assertIn(self.packet["brief"], request["message"]["content"])

    def test_existing_handoff_respects_newly_disabled_worker(self):
        local = self.folder / "router.local.json"
        local.write_text('{"disabled_workers":["carbon"]}', encoding="utf-8")
        args = ["native_handoff.py", "--handoff", str(self.handoff), "--agy", str(self.binary),
                "--config", str(local)]
        with patch.object(sys, "argv", args), patch.object(native_handoff, "run_handoff") as worker:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(native_handoff.main(), 1)
            worker.assert_not_called()

    def test_rejects_substitution_nonzero_exit_and_wrong_effort(self):
        for model, exit_code in [("unrelated-model", 0), (self.slug, 1)]:
            with self.subTest(model=model, exit_code=exit_code):
                with patch.object(subprocess, "run", side_effect=self.responses(model, exit_code)):
                    with self.assertRaises(ValueError):
                        native_handoff.run_handoff(self.packet, None, str(self.binary))
        catalog = dict(line.split(None, 1) for line in self.catalog.splitlines())
        with self.assertRaises(ValueError):
            native_handoff.select_model(self.packet, "gemini-4-carbon-high", catalog)
        hardest = dict(self.packet, effort="highest offered")
        self.assertEqual(native_handoff.select_model(hardest, "gemini-4-carbon-high", catalog)[2], "high")

    def test_stream_without_reported_model_preserves_result_and_labels_evidence(self):
        replies = self.responses(self.slug)
        events = [json.loads(line) for line in replies[1].stdout.splitlines()]
        del events[0]["init"]["model"]
        replies[1].stdout = "\n".join(map(json.dumps, events)).encode("utf-8")
        with patch.object(subprocess, "run", side_effect=replies):
            result = native_handoff.run_handoff(self.packet, None, str(self.binary))
        self.assertEqual(result["model_evidence"], "pinned_cli_argument")
        self.assertEqual(result["reply"], "独立建议")

    def test_missing_executable_stops_before_catalog_or_inference(self):
        with patch.object(subprocess, "run") as fake, self.assertRaises(RuntimeError):
            native_handoff.run_handoff(self.packet, None, str(self.folder / "missing.exe"))
        fake.assert_not_called()


class StandaloneCliTests(unittest.TestCase):
    def test_real_python_entrypoint_from_unrelated_working_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            config = folder / "局部配置.json"
            config.write_text('{"schema_version":1}', encoding="utf-8")
            # Preview only: a real Python process exercises its public entry point.
            result = subprocess.run(
                [sys.executable, "-B", "-X", "utf8", str(ROOT / "router.py"), "design",
                 "--config", str(config), "--hard-reason", "需要验证"],
                cwd=folder, capture_output=True, encoding="utf-8", timeout=15, check=True,
            )
            route = json.loads(result.stdout)
            self.assertEqual((route["status"], route["escalation_reason"]), ("preview", "需要验证"))


class SetupTests(unittest.TestCase):
    def test_fresh_init_binds_catalogs_independently_of_adjacent_local_settings(self):
        labels = [
            "Gemini 4 Neon Medium", "Gemini 4 Neon XHigh",
            "Gemini 4 Carbon Medium", "Gemini 4 Carbon High",
            "Claude Sonnet 5.5 Medium", "Claude Opus 5.5 High", "Claude Opus 5.5 Max",
        ]
        catalog = {native_handoff.normalize(label): label for label in labels}
        with tempfile.TemporaryDirectory() as temp, contextlib.ExitStack() as stack:
            folder = Path(temp)
            adjacent = folder / "existing-machine.json"
            adjacent.write_text('{"disabled_workers":["neon","claude"]}', encoding="utf-8")
            config = folder / "新电脑.json"
            cx = {
                "available": True, "chatgpt_login": True, "executable": str(folder / "codex.exe"),
                "missing_flags": [], "model_efforts": {"gpt-6.2-sol": ["medium"], "gpt-6.2-astra": ["medium", "high"]},
            }
            stack.enter_context(patch.object(subprocess, "Popen", side_effect=AssertionError("unexpected child")))
            stack.enter_context(patch.object(router_config, "LOCAL_CONFIG", adjacent))
            stack.enter_context(patch.object(setup_router, "codex_status", return_value=cx))
            stack.enter_context(patch.object(setup_router, "find_agy", return_value=str(folder / "agy.exe")))
            stack.enter_context(patch.object(setup_router, "list_models", return_value=catalog))
            result = setup_router.initialize(config)
            self.assertEqual((result["status"], result["inference_requests"]), ("full_preflight", 0))
            saved = router_config.load_config(config)
            self.assertEqual(saved["disabled_workers"], [])
            self.assertEqual(saved["profiles"]["gpt"]["hard"]["effort"], "high")
            self.assertEqual(saved["profiles"]["claude"]["hard"]["slug"], "claude-opus-5-5-max")
            self.assertEqual(setup_router.initialize(config)["config_action"], "unchanged")
            self.assertEqual(router_config.load_config()["disabled_workers"], ["neon", "claude"])

    def test_link_workspace_is_idempotent_and_preserves_edited_rule(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "研究工程"
            workspace.mkdir()
            old = workspace / "GEMINI.md"
            old.write_bytes(b"original user rules")
            result = setup_router.link_workspace(workspace)
            self.assertEqual(result["status"], "linked")
            self.assertEqual(setup_router.link_workspace(workspace)["status"], "already_linked")
            link = Path(result["rule"])
            self.assertIn(str(ROOT / "GEMINI.md"), link.read_text(encoding="utf-8"))
            link.write_bytes(b"user changed the linked rule")
            with self.assertRaises(ValueError):
                setup_router.link_workspace(workspace)
            self.assertEqual(link.read_bytes(), b"user changed the linked rule")
            self.assertEqual(old.read_bytes(), b"original user rules")

    def test_doctor_is_readonly_and_strict_partial_has_distinct_exit(self):
        cx = {"available": False, "executable": None, "chatgpt_login": False, "missing_flags": [], "model_efforts": {}}
        with tempfile.TemporaryDirectory() as temp, contextlib.ExitStack() as stack:
            config = Path(temp) / "个人配置.json"
            before = '{"schema_version":1,"disabled_workers":["carbon"]}'.encode("utf-8")
            config.write_bytes(before)
            stack.enter_context(patch.object(subprocess, "Popen", side_effect=AssertionError("unexpected child")))
            stack.enter_context(patch.object(setup_router, "codex_status", return_value=cx))
            stack.enter_context(patch.object(setup_router, "find_agy", side_effect=RuntimeError("模拟 CLI 缺失")))
            for strict, expected_code in [(False, 0), (True, 2)]:
                args = ["setup_router.py", "doctor", "--config", str(config)]
                if strict:
                    args.append("--strict")
                stdout = io.StringIO()
                with patch.object(sys, "argv", args), contextlib.redirect_stdout(stdout):
                    self.assertEqual(setup_router.main(), expected_code)
                report = json.loads(stdout.getvalue())
                self.assertEqual((report["status"], report["inference_requests"]), ("partial", 0))
                self.assertEqual(report["profiles"]["carbon.standard"], "disabled")
                self.assertEqual(config.read_bytes(), before)

    def test_local_init_is_idempotent_and_preserves_manual_disabled_workers(self):
        cx = {"available": False, "executable": None, "chatgpt_login": False, "missing_flags": [], "model_efforts": {}}
        with tempfile.TemporaryDirectory() as temp, contextlib.ExitStack() as stack:
            config = Path(temp) / "局部设置.json"
            config.write_text('{"schema_version":1,"disabled_workers":["carbon"]}', encoding="utf-8")
            stack.enter_context(patch.object(subprocess, "Popen", side_effect=AssertionError("unexpected child")))
            stack.enter_context(patch.object(setup_router, "codex_status", return_value=cx))
            stack.enter_context(patch.object(setup_router, "find_agy", side_effect=RuntimeError("模拟 CLI 缺失")))
            self.assertEqual(setup_router.initialize(config)["config_action"], "saved")
            self.assertEqual(setup_router.initialize(config)["config_action"], "unchanged")
            self.assertEqual(json.loads(config.read_text(encoding="utf-8"))["disabled_workers"], ["carbon"])
            self.assertEqual(list(Path(temp).iterdir()), [config])


if __name__ == "__main__":
    unittest.main()
