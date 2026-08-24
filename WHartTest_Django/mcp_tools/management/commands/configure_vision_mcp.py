import os

from asgiref.sync import async_to_sync
from django.core.management.base import BaseCommand, CommandError

from mcp_tools.models import RemoteMCPConfig
from mcp_tools.services import sync_mcp_tools


class Command(BaseCommand):
    help = "注册或更新Vision MCP远程服务，并同步可供Agent调用的工具列表"

    def add_arguments(self, parser):
        parser.add_argument("--name", default=os.environ.get("VISION_MCP_NAME", "vision-mcp"))
        parser.add_argument("--url", default=os.environ.get("VISION_MCP_URL", "http://vision-mcp:8010/mcp"))
        parser.add_argument("--authorization", default=os.environ.get("VISION_MCP_AUTHORIZATION", ""))
        parser.add_argument("--inactive", action="store_true")

    def handle(self, *args, **options):
        headers = {}
        if options["authorization"]:
            headers["Authorization"] = options["authorization"]
        config, created = RemoteMCPConfig.objects.update_or_create(
            name=options["name"],
            defaults={
                "url": options["url"],
                "transport": "streamable-http",
                "headers": headers,
                "is_active": not options["inactive"],
                "require_hitl": False,
            },
        )
        result = async_to_sync(sync_mcp_tools)(config)
        if not result.get("success"):
            raise CommandError(f"Vision MCP已保存但工具同步失败: {result.get('error', 'unknown error')}")
        action = "创建" if created else "更新"
        self.stdout.write(self.style.SUCCESS(
            f"已{action} {config.name}: {config.url}，同步工具 {result['tools_count']} 个"
        ))
