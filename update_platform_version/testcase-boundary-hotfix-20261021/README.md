# 用例审查“测试场景边界分析”热修复（2026-10-21）

该包为现有用例审查增加边界分析结构化输出，以及独立、高亮的 Excel 工作表。

- 工作表：测试场景边界分析，橙色标签、醒目标题。
- 七个角度：正向、反向、极值、异常、兼容性、移动响应式、回归。
- 矩阵：已覆盖绿色、缺口红色、有明确依据的豁免灰色；缺口分级着色。
- 已有报告不会变化，需要使用原始测试用例重新审查。不要上传审查报告作为源用例。

## 安装

将 ZIP 传入内网服务器，解压后进入本目录。在无正在执行的审查或其他后台任务时执行；脚本会重启 Backend，通常中断服务几十秒。

```bash
# 替换为 docker ps 中实际的 Backend 名称。
# 先预检：不安装、不重启、不调用模型、不创建业务任务。
bash apply.sh wharttest-backend --check

# 预检通过后安装；安装前会再次预检。
bash apply.sh wharttest-backend
```

本机新版环境的容器名为 wharttest-new-backend。脚本不传名称时会在这两个常用名称中自动查找；同时存在两者时必须显式指定。若当前用户无 Docker 权限，请使用有权限的账户或 sudo bash 执行。

只依赖 Bash、Docker 和容器已有的 Python/Django/openpyxl，无需联网、pip、编译或更新数据库。

## Skill 包

同目录的 test-case-clarity-review-skill-v1.3.0.zip 是最新版规则包。请按现有平台操作上传到 Skill Hub，审查时明确选择该包对应版本。部署脚本不改写 Skill Hub 记录，也不改变已有任务锁定的版本。

## 兼容性与保护

补丁只修改实际目标文件 /app/testcases/review_service.py 中三个函数：_review_chunk、_write_report、run_testcase_review；保留其他代码。使用目标容器的当前文件作为基线，逐段唯一匹配；与已知参考版本不兼容时停止，不使用本地 dev 整文件覆盖内网文件。

预检包括 Python 语法、真实运行环境导入、模型请求结构和内存 Excel 导出验证，核对 sheet、标签、标题、绿色/红色/灰色高亮及覆盖率公式。预检不会调用大模型。未返回分析时仍保留工作表，不会凭空补造覆盖数据。

若提示“目标代码与补丁不匹配”，请保留 backups/时间戳/ 下的 original.py、preflight.log、container.txt、mounts.json，供按内网实际代码适配。不要强行复制其他环境的整个文件。original.py 为源码，提供前可按组织要求检查其中是否含内嵌敏感信息。

安装前会保存完整原文件和独立 metadata.json（原/新 SHA256、目标路径、修改函数）。安装后重启并核验健康和导出，失败自动恢复并再次验证健康。源码使用文件或目录 bind mount 时修改对应宿主机文件；如果宿主机路径不可访问/不可写，或源码使用 Docker volume，则安全停止。

## 回滚

```bash
# 回滚最近一次成功安装
bash rollback.sh

# 或指定某次安装的备份目录
bash rollback.sh /绝对路径/testcase-boundary-hotfix-20261021/backups/时间戳
```

回滚先核对原文件哈希；若目标代码在安装后又发生其他变化，会停止，避免覆盖后续修复。回滚会重启 Backend。

## 持久性

Docker 容器可写层中的修改可保留到普通 restart，但重新创建容器/重新部署镜像会丢失。届时需要重新应用此补丁，或将补丁纳入正式镜像发布。不要把备份放在即将删除的容器里；本脚本备份位于服务器解压目录的 backups/ 下。

本包已在本机实际 Backend 的 Python 环境完成报告预检。内网镜像版本需由 --check 再次确认；未宣称已验证内网麒麟/ARM64 环境。
