from dataclasses import dataclass, field
import yaml


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
class Route:
    name: str
    from_chats: list[int]
    to_channels: list[int]


@dataclass
class AppConfig:
    telegram: TelegramConfig
    discord: DiscordConfig
    media: MediaConfig
    routes: list[Route]
    route_map: dict[int, list[int]] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.route_map = _build_route_map(self.routes)


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


def load_config(path: str = "data/config.yaml") -> AppConfig:
    with open(path) as f:
        data = yaml.safe_load(f)

    tg = data["telegram"]
    dc = data["discord"]
    media_data = data.get("media", {})
    catbox_data = media_data.get("catbox", {})

    routes = [
        Route(
            name=r["name"],
            from_chats=[int(c) for c in r["from"]],
            to_channels=[int(c) for c in r["to"]],
        )
        for r in data.get("routes", [])
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
        media=MediaConfig(
            max_upload_size_mb=int(media_data.get("max_upload_size_mb", 25)),
            catbox_max_upload_size_mb=int(media_data.get("catbox_max_upload_size_mb", 200)),
            max_file_size_mb=int(media_data.get("max_file_size_mb", 200)),
            catbox=CatboxConfig(
                enabled=bool(catbox_data.get("enabled", False)),
                userhash=str(catbox_data.get("userhash", "")),
            ),
        ),
        routes=routes,
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
            {"name": r.name, "from": r.from_chats, "to": r.to_channels}
            for r in config.routes
        ],
    }
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


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
