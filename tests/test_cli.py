from design_pipeline.cli import main


def test_cli_init_and_status(tmp_path, capsys):
    assert main(["init", str(tmp_path)]) == 0
    assert main(["status", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    # The CLI targets the `default` project when no project_id is passed;
    # the on-disk layout is `.design/<project_id>/state/...` since
    # multi-project support was added.
    assert '"project_id": "default"' in output
    assert (tmp_path / ".design" / "default" / "state" / "project-state.yaml").exists()
