# tests/test_config.py
import pytest
import yaml
from config import load_config, save_config, add_route, remove_route, Route


@pytest.fixture
def config_file(tmp_path):
    data = {
        "telegram": {"api_id": 123, "api_hash": "abc", "session_name": "test"},
        "discord": {"token": "tok", "commands_enabled": True},
        "media": {"max_upload_size_mb": 25, "catbox": {"enabled": False, "userhash": ""}},
        "routes": [
            {"name": "r1", "from": [-100111], "to": [999]},
            {"name": "r2", "from": [-100111, -100222], "to": [888, 777]},
        ],
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(data))
    return str(path)


def test_load_telegram_fields(config_file):
    config = load_config(config_file)
    assert config.telegram.api_id == 123
    assert config.telegram.api_hash == "abc"
    assert config.telegram.session_name == "test"


def test_load_discord_fields(config_file):
    config = load_config(config_file)
    assert config.discord.token == "tok"
    assert config.discord.commands_enabled is True


def test_load_media_fields(config_file):
    config = load_config(config_file)
    assert config.media.max_upload_size_mb == 25
    assert config.media.catbox.enabled is False
    assert config.media.catbox.userhash == ""


def test_load_routes(config_file):
    config = load_config(config_file)
    assert len(config.routes) == 2
    assert config.routes[0].name == "r1"
    assert config.routes[0].from_chats == [-100111]
    assert config.routes[0].to_channels == [999]


def test_route_map_merges_overlapping_sources(config_file):
    config = load_config(config_file)
    # -100111 appears in both r1 and r2
    assert set(config.route_map[-100111]) == {999, 888, 777}


def test_route_map_single_source(config_file):
    config = load_config(config_file)
    assert set(config.route_map[-100222]) == {888, 777}


def test_add_route_persists(config_file):
    config = load_config(config_file)
    new_route = Route(name="r3", from_chats=[-100333], to_channels=[666])
    add_route(config, new_route, config_file)
    reloaded = load_config(config_file)
    assert any(r.name == "r3" for r in reloaded.routes)
    assert 666 in reloaded.route_map[-100333]


def test_remove_route_existing(config_file):
    config = load_config(config_file)
    removed = remove_route(config, "r1", config_file)
    assert removed is True
    reloaded = load_config(config_file)
    assert all(r.name != "r1" for r in reloaded.routes)


def test_remove_route_nonexistent(config_file):
    config = load_config(config_file)
    removed = remove_route(config, "ghost", config_file)
    assert removed is False
    assert len(config.routes) == 2


def test_add_route_duplicate_name_raises(config_file):
    config = load_config(config_file)
    duplicate = Route(name="r1", from_chats=[-100999], to_channels=[111])
    with pytest.raises(ValueError, match="already exists"):
        add_route(config, duplicate, config_file)
