#!/bin/bash
echo "======================================================"
echo " 编译兼容 64KB 页的 jemalloc 修复包"
echo " ======================================================"
echo " ⚠️ 此脚本需在有网络的机器上运行，且需安装 Docker"

CURRENT_DIR=$(pwd)
OUTPUT_DIR="${CURRENT_DIR}/qdrant_fix_output"

# 清理旧的输出目录
rm -rf $OUTPUT_DIR
mkdir -p $OUTPUT_DIR

echo "[1/2] 正在启动 ARM64 模拟容器编译 jemalloc (需下载基础镜像和源码，请耐心等待)..."
# 使用 ARM64 Ubuntu 容器，并传入关键参数 JEMALLOC_SYS_WITH_LG_PAGE=16 强制兼容 64KB 页
docker run --rm -v "${OUTPUT_DIR}:/output" \
  arm64v8/ubuntu:22.04 \
  bash -c "
    apt-get update && \
    apt-get install -y build-essential wget && \
    echo '正在下载 jemalloc 5.3.0 源码...' && \
    wget -q https://github.com/jemalloc/jemalloc/releases/download/5.3.0/jemalloc-5.3.0.tar.bz2 && \
    tar -xjf jemalloc-5.3.0.tar.bz2 && \
    cd jemalloc-5.3.0 && \
    echo '正在编译 (配置 LG_PAGE=16 对应 64KB 页)...' && \
    export JEMALLOC_SYS_WITH_LG_PAGE=16 && \
    ./configure --with-lg-page=16 > /dev/null && \
    make -j$(nproc) > /dev/null && \
    cp lib/libjemalloc.so.2 /output/libjemalloc.so.2 && \
    echo '✅ 编译完成！'
  "

if [ -f "${OUTPUT_DIR}/libjemalloc.so.2" ]; then
    echo "[2/2] 正在打包修复文件为 tar 包..."
    cd $OUTPUT_DIR
    tar -cvf qdrant_64k_fix.tar libjemalloc.so.2
    cd $CURRENT_DIR

    echo "======================================================"
    echo " 🎉 修复包构建并打包成功！"
    echo " 文件位置: ${OUTPUT_DIR}/qdrant_64k_fix.tar"
    echo " 文件大小: $(du -sh ${OUTPUT_DIR}/qdrant_64k_fix.tar | cut -f1)"
    echo " ======================================================"
    echo ""
    echo "👉 下一步操作："
    echo " 1. 将 ${OUTPUT_DIR}/qdrant_64k_fix.tar 拷贝到你的内网服务器上（如 /tmp 目录）。"
    echo " 2. 在内网服务器上运行下方提供的 '2_apply_fix_intranet.sh' 脚本。"
else
    echo "❌ 编译失败，未生成 libjemalloc.so.2 文件。请检查 Docker 是否正常运行及网络连接。"
fi
