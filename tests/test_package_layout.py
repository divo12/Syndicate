"""The package is layered: controllers, models, services, repositories, adapters."""

from importlib import import_module


def test_controller_entry_stays_as_cli_module() -> None:
    module = import_module("syndicate.cli")
    assert callable(module.main)
    assert callable(import_module("syndicate.controllers.preflight").dispatch)


def test_model_layer_exports_contracts() -> None:
    envelope = import_module("syndicate.models.envelope")
    budget = import_module("syndicate.models.budget")
    model_config = import_module("syndicate.models.model_config")
    runtime = import_module("syndicate.models.runtime")
    baseline = import_module("syndicate.models.baseline")
    shell = import_module("syndicate.models.shell")
    assert envelope.CommandReceipt is not None
    assert budget.BudgetCap is not None
    assert model_config.ModelSettings is not None
    assert runtime.RuntimeRequest is not None
    assert baseline.BaselineManifest is not None
    assert shell.ShellRequest is not None


def test_service_layer_exports_use_cases() -> None:
    preflight = import_module("syndicate.services.preflight")
    runtime = import_module("syndicate.services.runtime")
    stock = import_module("syndicate.services.stock")
    benchmark = import_module("syndicate.services.benchmark")
    assert callable(preflight.preflight)
    assert callable(runtime.run_on_controller)
    assert callable(stock.emit_cleanup_receipt)
    assert callable(benchmark.verify_with_harbor)


def test_repository_and_adapter_layers() -> None:
    manifest = import_module("syndicate.repositories.benchmark_manifest")
    shell = import_module("syndicate.adapters.e2b_shell")
    harbor = import_module("syndicate.adapters.harbor_agent")
    adapter = import_module("syndicate.adapters.harbor_adapter")
    assert manifest.BenchmarkManifest is not None
    assert shell.E2BShell is not None
    assert harbor.HarborAgent is not None
    assert adapter.SyndicateNexAUAgent is not None
