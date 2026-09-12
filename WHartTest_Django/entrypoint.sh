#!/bin/bash

# 确保脚本在任何命令失败时退出
set -e

# 所有运行日志和 PID 均落到持久化 /app/data，不占用容器 /var。
mkdir -p /app/data/logs /app/data/run

# 1. 数据库迁移
echo "Applying database migrations..."
python manage.py migrate --noinput

# 2. 创建默认管理员用户
echo "Creating default admin user if it does not exist..."
python manage.py init_admin

# 3. 启动 supervisord 来管理所有服务
echo "Starting supervisord..."
exec supervisord -c /app/supervisord.conf
