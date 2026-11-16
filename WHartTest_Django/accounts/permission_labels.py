"""权限目录的中文显示名称；不修改数据库权限标识。"""
import re

MODEL_LABELS = {
    "api interface constraint": "接口参数约束",
    "benchmark case": "基准案例",
    "benchmark dataset version": "基准数据集版本",
    "benchmark prepare job": "基准准备任务",
    "benchmark repository snapshot": "基准仓库快照",
    "benchmark run binding": "基准运行绑定",
    "requirement comparison": "需求对比",
    "skill agent task binding": "技能智能体任务绑定",
    "skill assessment session": "技能测评会话",
    "skill evaluation profile": "技能测评配置",
}
TERMS = {
    "GitLab": "代码托管", "Vision": "视觉", "Skills": "技能", "Skill": "技能",
    "Agent": "智能体", "LLM": "大模型", "MCP": "工具协议", "API": "接口",
    "UI": "界面", "Token": "令牌", "Cron": "定时", "KEY": "密钥",
}


def chinese_label(value, fallback):
    label = str(value or "").strip()
    label = MODEL_LABELS.get(label.lower(), label)
    for source, target in TERMS.items():
        label = re.sub(re.escape(source), target, label, flags=re.IGNORECASE)
    return label if label and not re.search(r"[A-Za-z]", label) else fallback


def permission_dependencies(permission_ids):
    """代码审查的项目选择需要查看项目；不增加任何项目成员关系。"""
    from django.contrib.auth.models import Permission
    ids = set(permission_ids)
    if Permission.objects.filter(pk__in=ids, content_type__app_label="code_analysis").exists():
        project_view = Permission.objects.filter(
            content_type__app_label="projects", codename="view_project"
        ).first()
        if project_view:
            ids.add(project_view.pk)
    return sorted(ids)
