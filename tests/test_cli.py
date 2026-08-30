from design_pipeline.cli import main


def test_cli_init_and_status(tmp_path, capsys):
    assert main(["init", str(tmp_path)]) == 0
    assert main(["status", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert f'"project_id": "{tmp_path.name}"' in output
    assert (tmp_path / ".design" / "state" / "project-state.yaml").exists()
