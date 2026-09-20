from job.agent.github_pr import github_client_from_env


def test_github_client_from_env_none_without_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert github_client_from_env() is None


def test_github_client_from_env_builds_client(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    client = github_client_from_env(repo="HenryDMcGee/internship-watch")
    assert client is not None
    assert client.repo == "HenryDMcGee/internship-watch"
    assert client.token == "tok"
