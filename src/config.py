from dataclasses import dataclass, field
import os
import yaml


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_str(name: str, default: str) -> str:
    return os.environ.get(name, default) or default


@dataclass
class CatboxConfig:
    enabled: bool = False
    userhash: str = ""


@dataclass
class MediaConfig:
    max_upload_size_mb: int = 25
    catbox_max_upload_size_mb: int = 200
    max_file_size_mb: int = 200
    catbox: CatboxConfig = field(default_factory=CatboxConfig)
    # Disk mode: when save_to_disk is True, downloads are written to cache_dir
    # and uploaded from disk instead of being held in RAM.
    save_to_disk: bool = False
    cache_dir: str = "data/media_cache"


@dataclass
class TelegramConfig:
    api_id: int = 0
    api_hash: str = ""
    session_name: str = "tg_session"


@dataclass
class DiscordConfig:
    token: str = ""
    commands_enabled: bool = True


@dataclass
class DiscordUserConfig:
    token: str = ""


@dataclass
class DiscordRoute:
    name: str
    from_channel: int
    to_channel: int
    media_types: list[str] = field(default_factory=lambda: ["image", "video"])
    forward_delay_seconds: float = 1.0
    track_message_ids: bool = True


@dataclass
class Route:
    name: str
    from_chats: list[int]
    to_channels: list[int]
    # When True (and media.save_to_disk is enabled), keep the downloaded file
    # in the cache dir after forwarding instead of deleting it.
    store: bool = False


@dataclass
class AppConfig:
    telegram: TelegramConfig
    discord: DiscordConfig
    media: MediaConfig
    routes: list[Route]
    discord_user: DiscordUserConfig = field(default_factory=DiscordUserConfig)
    discord_routes: list[DiscordRoute] = field(default_factory=list)
    route_map: dict[int, list[int]] = field(default_factory=dict, init=False)
    discord_route_map: dict[int, list[DiscordRoute]] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.route_map = _build_route_map(self.routes)
        self.discord_route_map = _build_discord_route_map(self.discord_routes)


def _build_route_map(routes: list[Route]) -> dict[int, list[int]]:
    route_map: dict[int, list[int]] = {}
    for route in routes:
        for chat_id in route.from_chats:
            if chat_id not in route_map:
                route_map[chat_id] = []
            for channel_id in route.to_channels:
                if channel_id not in route_map[chat_id]:
                    route_map[chat_id].append(channel_id)
    return route_map


def _build_discord_route_map(routes: list[DiscordRoute]) -> dict[int, list[DiscordRoute]]:
    route_map: dict[int, list[DiscordRoute]] = {}
    for route in routes:
        if route.from_channel not in route_map:
            route_map[route.from_channel] = []
        route_map[route.from_channel].append(route)
    return route_map


def load_config(path: str = "data/config.yaml") -> AppConfig:
    with open(path) as f:
        data = yaml.safe_load(f)

    tg = data["telegram"]
    dc = data["discord"]
    dc_user = data.get("discord_user", {})
    media_data = data.get("media", {})
    catbox_data = media_data.get("catbox", {})

    routes = [
        Route(
            name=r["name"],
            from_chats=[int(c) for c in r["from"]],
            to_channels=[int(c) for c in r["to"]],
            store=bool(r.get("store", False)),
        )
        for r in data.get("routes", [])
    ]

    discord_routes = [
        DiscordRoute(
            name=r["name"],
            from_channel=int(r["from_channel"]),
            to_channel=int(r["to_channel"]),
            media_types=r.get("media_types", ["image", "video"]),
            forward_delay_seconds=float(r.get("forward_delay_seconds", 1.0)),
            track_message_ids=bool(r.get("track_message_ids", True)),
        )
        for r in data.get("discord_routes", [])
    ]

    return AppConfig(
        telegram=TelegramConfig(
            api_id=int(tg["api_id"]),
            api_hash=str(tg["api_hash"]),
            session_name=tg.get("session_name", "tg_session"),
        ),
        discord=DiscordConfig(
            token=str(dc["token"]),
            commands_enabled=bool(dc.get("commands_enabled", True)),
        ),
        discord_user=DiscordUserConfig(
            token=str(dc_user.get("token", "")),
        ),
        media=MediaConfig(
            max_upload_size_mb=int(media_data.get("max_upload_size_mb", 25)),
            catbox_max_upload_size_mb=int(media_data.get("catbox_max_upload_size_mb", 200)),
            max_file_size_mb=int(media_data.get("max_file_size_mb", 200)),
            catbox=CatboxConfig(
                enabled=bool(catbox_data.get("enabled", False)),
                userhash=str(catbox_data.get("userhash", "")),
            ),
            save_to_disk=_env_bool("SAVE_MEDIA_TO_DISK"),
            cache_dir=_env_str("MEDIA_CACHE_DIR", "data/media_cache"),
        ),
        routes=routes,
        discord_routes=discord_routes,
    )


def save_config(config: AppConfig, path: str = "data/config.yaml") -> None:
    data = {
        "telegram": {
            "api_id": config.telegram.api_id,
            "api_hash": config.telegram.api_hash,
            "session_name": config.telegram.session_name,
        },
        "discord": {
            "token": config.discord.token,
            "commands_enabled": config.discord.commands_enabled,
        },
        **({"discord_user": {"token": config.discord_user.token}} if config.discord_user.token else {}),
        "media": {
            "max_upload_size_mb": config.media.max_upload_size_mb,
            "catbox_max_upload_size_mb": config.media.catbox_max_upload_size_mb,
            "max_file_size_mb": config.media.max_file_size_mb,
            "catbox": {
                "enabled": config.media.catbox.enabled,
                "userhash": config.media.catbox.userhash,
            },
        },
        "routes": [
            {"name": r.name, "from": r.from_chats, "to": r.to_channels, "store": r.store}
            for r in config.routes
        ],
        "discord_routes": [
            {
                "name": r.name,
                "from_channel": r.from_channel,
                "to_channel": r.to_channel,
                "media_types": r.media_types,
                "forward_delay_seconds": r.forward_delay_seconds,
                "track_message_ids": r.track_message_ids,
            }
            for r in config.discord_routes
        ],
    }
    # Atomic write: write to a temp file then replace, so a crash mid-write
    # can never corrupt the only config file.
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
    os.replace(tmp_path, path)


def add_route(config: AppConfig, route: Route, path: str = "data/config.yaml") -> None:
    if any(r.name == route.name for r in config.routes):
        raise ValueError(f"Route '{route.name}' already exists")
    config.routes.append(route)
    config.route_map = _build_route_map(config.routes)
    save_config(config, path)


def remove_route(config: AppConfig, name: str, path: str = "data/config.yaml") -> bool:
    before = len(config.routes)
    config.routes = [r for r in config.routes if r.name != name]
    if len(config.routes) == before:
        return False
    config.route_map = _build_route_map(config.routes)
    save_config(config, path)
    return True
