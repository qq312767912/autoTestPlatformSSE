#!/bin/bash
# WHartTest AI 智能安装助手
# 纯 Bash 实现，无需 Python 环境

if [ -z "${BASH_VERSION:-}" ]; then
    echo "This script requires bash. Please run: bash ai_install.sh" >&2
    exit 1
fi

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# 全局变量
API_URL="https://ccccccc.openai/v1"
API_KEY="sk-xxxxxxxxxxxxx"
MODEL_NAME="gpt-5.4"
SYSTEM_PROMPT=""
ASSUME_YES=0
APPROVE_ALL=0
LOG_DIR="${AI_INSTALL_LOG_DIR:-data/logs}"
LOG_FILE=""
DEBUG_LOG="${AI_INSTALL_DEBUG:-0}"
MAX_TOKENS="${AI_INSTALL_MAX_TOKENS:-30000}"
TEMPERATURE="${AI_INSTALL_TEMPERATURE:-0.7}"
INCLUDE_TEMPERATURE="${AI_INSTALL_INCLUDE_TEMPERATURE:-1}"
TIMEOUT="${AI_INSTALL_TIMEOUT:-120}"
CONNECT_TIMEOUT="${AI_INSTALL_CONNECT_TIMEOUT:-30}"
MAX_TOOL_ROUNDS="${AI_INSTALL_MAX_TOOL_ROUNDS:-8}"
AI_RAW_RESPONSE_BODY=""
API_TEST_ERROR=""

# 临时目录（兼容 Linux/macOS/Git Bash/WSL）
TMP_DIR="${TMPDIR:-/tmp}"
if [ ! -d "$TMP_DIR" ] || [ ! -w "$TMP_DIR" ]; then
    TMP_DIR="/tmp"
fi
if [ ! -d "$TMP_DIR" ] || [ ! -w "$TMP_DIR" ]; then
    TMP_DIR="."
fi

CONVERSATION_FILE="$TMP_DIR/ai_install_conversation_$$.json"

# sed 扩展正则开关（macOS/GNU: -E，busybox 常见: -r）
SED_EXTENDED_FLAG="-E"
if ! printf 'x' | sed -E 's/x/x/' >/dev/null 2>&1; then
    SED_EXTENDED_FLAG="-r"
fi

# JSON 解析后端检测（启动时运行一次，避免每次调用都探测）
JSON_BACKEND="none"
HAS_PERL=0
PY_CMD=""

detect_json_backend() {
    if command -v jq >/dev/null 2>&1; then
        JSON_BACKEND="jq"
    elif command -v python3 >/dev/null 2>&1; then
        JSON_BACKEND="python"
        PY_CMD="python3"
    elif command -v python >/dev/null 2>&1; then
        JSON_BACKEND="python"
        PY_CMD="python"
    elif command -v perl >/dev/null 2>&1; then
        JSON_BACKEND="perl"
    fi
    command -v perl >/dev/null 2>&1 && HAS_PERL=1
}

# 清理函数
cleanup() {
    rm -f "$CONVERSATION_FILE"
}
trap cleanup EXIT

# 打印带颜色的消息
print_color() {
    local color=$1
    shift
    printf '%b%b%b\n' "$color" "$*" "$NC"
}

# 生成不带换行的彩色提示词（用于 read -p）
prompt_color() {
    local color=$1
    shift
    printf '%b%b%b' "$color" "$*" "$NC"
}

# 打印标题
print_header() {
    printf '\n'
    print_color "$CYAN" "=========================================="
    print_color "$BOLD$CYAN" "$1"
    print_color "$CYAN" "=========================================="
    printf '\n'
}

# 初始化日志文件（默认写入 data/logs）
init_log() {
    local ts
    ts=$(date '+%Y%m%d_%H%M%S' 2>/dev/null || echo "unknown_time")

    mkdir -p "$LOG_DIR" 2>/dev/null || true
    LOG_FILE="$LOG_DIR/ai_install_${ts}_$$.log"

    # 如果目标目录不可写，回退到 /tmp
    if ! ( : > "$LOG_FILE" ) 2>/dev/null; then
        LOG_DIR="$TMP_DIR"
        LOG_FILE="$TMP_DIR/ai_install_${ts}_$$.log"
        : > "$LOG_FILE" 2>/dev/null || true
    fi
}

log_line() {
    local level="$1"
    shift
    local ts
    ts=$(date '+%F %T' 2>/dev/null || echo "unknown_time")
    [ -z "${LOG_FILE:-}" ] && return 0
    printf '[%s] [%s] %s\n' "$ts" "$level" "$(redact_secrets "$*")" >> "$LOG_FILE" 2>/dev/null || true
}

# 构建系统提示词
build_system_prompt() {
    SYSTEM_PROMPT="你是一个助手。

工作规则：
1. 先理解用户需要什么帮助；如果需求不明确，先简短追问
2. 需要查看环境、文件、版本、日志、目录或命令结果时，直接调用系统提供的工具，不要猜测结果
3. 调用工具前先用一句简短的话说明要做什么
4. 不要输出 JSON 命令，也不要输出 Markdown 代码块里的命令
5. 不要伪造命令输出、文件内容、版本号或执行结果
6. 系统会处理危险命令的执行确认，除非确实需要用户做选择，否则不要重复询问是否执行
7. 回复保持简洁

现在开始对话。"
}

# 脱敏敏感信息（避免把密钥/令牌发给 AI）
redact_secrets() {
    local text="$1"
    printf '%s' "$text" | sed "$SED_EXTENDED_FLAG" \
        -e 's/(AI_API_KEY=).*/\1***REDACTED***/g' \
        -e 's/(OPENAI_API_KEY=).*/\1***REDACTED***/g' \
        -e 's/(DEEPSEEK_API_KEY=).*/\1***REDACTED***/g' \
        -e 's/(DASHSCOPE_API_KEY=).*/\1***REDACTED***/g' \
        -e 's/(Authorization: Bearer )[A-Za-z0-9._-]+/\1***REDACTED***/g' \
        -e 's/(Bearer )[A-Za-z0-9._-]+/\1***REDACTED***/g' \
        -e 's/sk-[A-Za-z0-9]{10,}/sk-***REDACTED***/g'
}

# 判断单条命令是否需要用户确认（修改性/敏感信息）
# 使用 bash 内建 [[ =~ ]] 避免 fork 子进程
command_segment_needs_confirmation() {
    local cmd="$1"
    cmd="${cmd#"${cmd%%[![:space:]]*}"}"  # trim leading
    cmd="${cmd%"${cmd##*[![:space:]]}"}"  # trim trailing
    [[ -z "$cmd" ]] && return 1

    # 去除 >/dev/null 等无害重定向后，检查是否还有写文件重定向
    local redirect_check
    redirect_check="$(printf '%s' "$cmd" | sed "$SED_EXTENDED_FLAG" \
        -e 's/(^|[[:space:]])[0-9]*>>?[[:space:]]*\/dev\/null([[:space:]]|$)/ /g' \
        -e 's/(^|[[:space:]])&>>?[[:space:]]*\/dev\/null([[:space:]]|$)/ /g' \
        -e 's/(^|[[:space:]])[0-9]*>&[0-9-]+([[:space:]]|$)/ /g')"

    # 写文件/覆盖输出
    [[ "$redirect_check" =~ (^|[^\\])(>>?|'&>>?'|'&>') ]] && return 0
    # tee 落盘
    [[ "$cmd" =~ (^|[^\\])\|[[:space:]]*tee([[:space:]]|$) ]] && return 0

    # 转小写以实现大小写无关匹配
    local cmd_lower="${cmd,,}"

    # 敏感信息泄露
    [[ "$cmd_lower" =~ (^|[[:space:]])(env|printenv)([[:space:]]|$) ]] && return 0
    [[ "$cmd_lower" =~ (^|[[:space:]])cat[[:space:]]+(\.env|.*\.env)([[:space:]]|$) ]] && return 0

    # 修改性命令：提权、文件操作、包管理、git、docker、系统服务
    [[ "$cmd_lower" =~ (^|[[:space:]])(sudo|su|rm|mv|cp|mkdir|rmdir|chmod|chown|chgrp|ln|truncate|dd)([[:space:]]|$) ]] && return 0
    [[ "$cmd_lower" =~ (^|[[:space:]])(apt|apt-get|yum|dnf|pacman|brew|pip|pip3|poetry|npm|pnpm|yarn)([[:space:]]|$) ]] && return 0
    [[ "$cmd_lower" =~ (^|[[:space:]])git[[:space:]]+(clone|checkout|switch|pull|push|reset|clean|rebase|merge|commit|tag)([[:space:]]|$) ]] && return 0
    [[ "$cmd_lower" =~ (^|[[:space:]])docker[[:space:]]+(run|build|pull|push|rm|rmi|volume|network)([[:space:]]|$) ]] && return 0
    [[ "$cmd_lower" =~ (^|[[:space:]])docker[[:space:]]+compose[[:space:]]+(up|down|build|pull|push|rm)([[:space:]]|$) ]] && return 0
    [[ "$cmd_lower" =~ (^|[[:space:]])docker-compose[[:space:]]+(up|down|build|pull|push|rm|start|stop|restart)([[:space:]]|$) ]] && return 0
    [[ "$cmd_lower" =~ (^|[[:space:]])(systemctl|service)([[:space:]]|$) ]] && return 0

    return 1
}

split_command_for_confirmation() {
    printf '%s' "$1" | perl -0pe 's/\r\n/\n/g; s/\r/\n/g; s/&&/\n/g; s/\|\|/\n/g; s/;/\n/g'
}

# 判断命令是否需要用户确认（支持只读串联命令）
command_needs_confirmation() {
    local cmd="$1"
    cmd="${cmd#"${cmd%%[![:space:]]*}"}"
    cmd="${cmd%"${cmd##*[![:space:]]}"}"
    [[ -z "$cmd" ]] && return 1

    if [[ "$cmd" =~ '&&'|'||'|(^|[^\\])';' ]]; then
        local segment=""
        while IFS= read -r segment; do
            segment="${segment#"${segment%%[![:space:]]*}"}"
            segment="${segment%"${segment##*[![:space:]]}"}"
            [[ -z "$segment" ]] && continue
            if command_segment_needs_confirmation "$segment"; then
                return 0
            fi
        done < <(split_command_for_confirmation "$cmd")
        return 1
    fi

    command_segment_needs_confirmation "$cmd"
}

# 用户拒绝命令时的反馈（全局变量）
USER_REJECT_FEEDBACK=""

can_read_from_tty() {
    [ -r /dev/tty ] && [ -w /dev/tty ]
}

prompt_read() {
    local __resultvar="$1"
    local prompt="$2"
    local value=""

    if can_read_from_tty; then
        printf '%b' "$prompt" > /dev/tty
        IFS= read -r value < /dev/tty || true
    elif [ -t 0 ]; then
        read -r -p "$prompt" value || true
    else
        return 1
    fi

    printf -v "$__resultvar" '%s' "$value"
    return 0
}

confirm_command() {
    local cmd="$1"
    USER_REJECT_FEEDBACK=""

    if [ "${APPROVE_ALL:-0}" = "1" ] || [ "${ASSUME_YES:-0}" = "1" ]; then
        return 0
    fi

    if ! can_read_from_tty && [ ! -t 0 ]; then
        print_color "$YELLOW" "[警告] 当前没有可用的交互终端，已跳过需要确认的命令：$cmd" >&2
        return 1
    fi

    echo "" >&2
    print_color "$YELLOW" "[警告] 该命令可能会修改系统或泄露敏感信息：" >&2
    print_color "$BOLD$YELLOW" "  $cmd" >&2
    local choice=""
    prompt_read choice "$(prompt_color "$GREEN" "是否允许执行？[y]允许 [n]拒绝 [a]本次全部允许 (默认 n): ")" || true

    case "$choice" in
        y|Y|yes|YES)
            return 0
            ;;
        a|A)
            APPROVE_ALL=1
            print_color "$YELLOW" "[警告] 已选择 \"本次全部允许\"，后续将不再逐条确认（请谨慎）" >&2
            return 0
            ;;
        *)
            print_color "$CYAN" "已拒绝执行该命令。" >&2
            # 询问用户是否有修正建议
            local feedback=""
            prompt_read feedback "$(prompt_color "$CYAN" "请输入修正建议（直接回车跳过）: ")" || true
            if [ -n "$feedback" ]; then
                USER_REJECT_FEEDBACK="$feedback"
            fi
            return 1
            ;;
    esac
}

# 配置 API
setup_api() {
    print_header "AI 智能安装助手"

    if [ -z "$API_URL" ] || [ -z "$API_KEY" ] || [ -z "$MODEL_NAME" ]; then
        print_color "$RED" "[错误] 脚本内置配置不完整，请先编辑 ai_install.sh 顶部的 API_URL、API_KEY、MODEL_NAME"
        return 1
    fi

    print_color "$GREEN" "[OK] 使用内置配置: $MODEL_NAME @ ${API_URL}"
    print_color "$YELLOW" "正在测试 API 连接..."
    if test_api_connection; then
        print_color "$GREEN" "[OK] API 连接成功\n"
        return 0
    fi

    print_color "$RED" "[错误] 内置配置连接失败，请直接修改 ai_install.sh 顶部配置后重试。\n"
    if [ -n "$API_TEST_ERROR" ]; then
        print_color "$YELLOW" "原因: $API_TEST_ERROR"
    fi
    return 1
}

# 测试 API 连接
test_api_connection() {
    API_TEST_ERROR=""
    local payload="{\"model\":\"$MODEL_NAME\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"max_tokens\":10}"
    local payload_file="$TMP_DIR/ai_install_test_payload_$$.json"
    printf '%s' "$payload" > "$payload_file"

    local response=$(curl -sS -w "\n%{http_code}" --connect-timeout "$CONNECT_TIMEOUT" --max-time "$TIMEOUT" -X POST "$API_URL/chat/completions" \
        -H "Authorization: Bearer $API_KEY" \
        -H "Content-Type: application/json" \
        -H "Expect:" \
        --data-binary @"$payload_file" 2>/dev/null)

    rm -f "$payload_file" 2>/dev/null || true

    split_http_response "$response"

    if [ "$HTTP_CODE" != "200" ]; then
        local error_msg=""
        error_msg=$(extract_error_message "$HTTP_BODY")
        if [ -n "$error_msg" ]; then
            API_TEST_ERROR="$error_msg"
        else
            API_TEST_ERROR="HTTP 状态码: $HTTP_CODE"
        fi
        return 1
    fi

    # HTTP 200 即视为连接成功，不要求响应必须有 content
    return 0
}

# 去除 ANSI 颜色代码 + 控制字符（合并为单个函数，避免双重管道）
sanitize_text() {
    if (( HAS_PERL )); then
        printf '%s' "$1" | perl -pe '
            s/\e\[[0-?]*[ -\/]*[@-~]//g;
            s/\e\][^\a]*(?:\a|\e\\)//g;
            s/\eP.*?\e\\//gs;
            s/\e[@-Z\\-_]//g;
            s/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]//g;
        '
    else
        local esc
        esc=$(printf '\033')
        printf '%s' "$1" \
            | sed "s/${esc}\[[0-9;?]*[ -\\/]*[@-~]//g; s/${esc}//g" \
            | tr -d '\000-\010\013\014\016-\037\177'
    fi
}

# JSON 转义函数
json_escape() {
    local string
    string=$(sanitize_text "$1")
    string="${string//\\/\\\\}"
    string="${string//\"/\\\"}"
    string="${string//$'\n'/\\n}"
    string="${string//$'\r'/\\r}"
    string="${string//$'\t'/\\t}"
    printf '%s' "$string"
}

append_history_message_json() {
    local message_json="$1"
    [ -z "$message_json" ] && return 0

    if [ -f "$CONVERSATION_FILE" ] && [ -s "$CONVERSATION_FILE" ]; then
        printf ',%s\n' "$message_json" >> "$CONVERSATION_FILE"
    else
        printf '%s\n' "$message_json" > "$CONVERSATION_FILE"
    fi
}

append_user_message_to_history() {
    local user_message="$1"
    local sanitized_user_message
    sanitized_user_message="$(redact_secrets "$user_message")"
    append_history_message_json "{\"role\":\"user\",\"content\":\"$(json_escape "$sanitized_user_message")\"}"
}

append_assistant_text_to_history() {
    local ai_message="$1"
    append_history_message_json "{\"role\":\"assistant\",\"content\":\"$(json_escape "$ai_message")\"}"
}

append_assistant_tool_calls_to_history() {
    local ai_message="$1"
    local tool_calls_json="$2"

    if [ -n "$ai_message" ]; then
        append_history_message_json "{\"role\":\"assistant\",\"content\":\"$(json_escape "$ai_message")\",\"tool_calls\":$tool_calls_json}"
    else
        append_history_message_json "{\"role\":\"assistant\",\"content\":null,\"tool_calls\":$tool_calls_json}"
    fi
}

append_tool_result_to_history() {
    local tool_call_id="$1"
    local tool_output="$2"
    append_history_message_json "{\"role\":\"tool\",\"tool_call_id\":\"$(json_escape "$tool_call_id")\",\"content\":\"$(json_escape "$tool_output")\"}"
}

build_messages_json() {
    local messages="[{\"role\":\"system\",\"content\":\"$(json_escape "$SYSTEM_PROMPT")\"}"

    if [ -f "$CONVERSATION_FILE" ]; then
        local history
        history=$(cat "$CONVERSATION_FILE")
        if [ -n "$history" ]; then
            messages="$messages,$history"
        fi
    fi

    messages="$messages]"
    printf '%s' "$messages"
}

get_shell_tool_definitions_json() {
    cat <<'EOF'
[{"type":"function","function":{"name":"run_shell_command","description":"Run one bash command in the current working directory and return stdout and stderr. Use this whenever you need to inspect the environment, list files, check versions, or perform installation steps.","parameters":{"type":"object","properties":{"command":{"type":"string","description":"A single bash command to execute."}},"required":["command"],"additionalProperties":false}}}]
EOF
}

build_request_payload() {
    local messages_json="$1"
    local temp_part=""
    if [ "${INCLUDE_TEMPERATURE:-1}" = "1" ]; then
        temp_part="\"temperature\": $TEMPERATURE,"
    fi

    printf '{"model":"%s","messages":%s,%s"max_tokens":%s,"stream":true,"tools":%s,"tool_choice":"auto"}' \
        "$MODEL_NAME" "$messages_json" "$temp_part" "$MAX_TOKENS" "$(get_shell_tool_definitions_json)"
}

# 将 SSE 流式响应聚合为标准非流式 JSON（兼容后续所有 extract_* 函数）
parse_sse_to_json() {
    if [ -n "$PY_CMD" ]; then
        "$PY_CMD" -c '
import json,sys
content="";tc={}
for line in sys.stdin:
    line=line.strip()
    if line=="data: [DONE]" or not line.startswith("data: "): continue
    try: chunk=json.loads(line[6:])
    except: continue
    delta=(chunk.get("choices") or [{}])[0].get("delta") or {}
    if delta.get("content"): content+=delta["content"]
    for t in delta.get("tool_calls") or []:
        i=t.get("index",0)
        if i not in tc: tc[i]={"id":"","type":"function","function":{"name":"","arguments":""}}
        if t.get("id"): tc[i]["id"]=t["id"]
        fn=t.get("function") or {}
        if fn.get("name"): tc[i]["function"]["name"]=fn["name"]
        tc[i]["function"]["arguments"]+=fn.get("arguments","")
msg={"content":content if content else None,"role":"assistant"}
if tc: msg["tool_calls"]=[tc[i] for i in sorted(tc)]
print(json.dumps({"choices":[{"message":msg,"finish_reason":"stop"}]}))
' 2>/dev/null
    elif (( HAS_PERL )); then
        perl -MJSON::PP -e '
            my($content,%tc)=("");
            while(<STDIN>){
                chomp; last if /^data: \[DONE\]/; next unless s/^data: //;
                my $c; eval{$c=decode_json($_);1} or next;
                my $d=($c->{choices}[0]||{})->{delta}||{};
                $content.=$d->{content}//"";
                for my $t(@{$d->{tool_calls}||[]}){
                    my $i=$t->{index}//0;
                    $tc{$i}//={id=>"",type=>"function",function=>{name=>"",arguments=>""}};
                    $tc{$i}{id}=$t->{id} if $t->{id};
                    my $fn=$t->{function}||{};
                    $tc{$i}{function}{name}=$fn->{name} if $fn->{name};
                    $tc{$i}{function}{arguments}.=$fn->{arguments}//"";
                }
            }
            my $msg={content=>length($content)?$content:undef,role=>"assistant"};
            $msg->{tool_calls}=[map{$tc{$_}}sort{$a<=>$b}keys%tc] if %tc;
            print encode_json({choices=>[{message=>$msg,finish_reason=>"stop"}]});
        ' 2>/dev/null
    fi
}

post_chat_completion_payload() {
    local payload_file="$1"
    local sse_file="$TMP_DIR/ai_sse_$$.txt"
    local http_code
    http_code=$(curl -sS -w "%{http_code}" -o "$sse_file" \
        --connect-timeout "$CONNECT_TIMEOUT" --max-time "$TIMEOUT" \
        -X POST "$API_URL/chat/completions" \
        -H "Authorization: Bearer $API_KEY" \
        -H "Content-Type: application/json" \
        -H "Expect:" \
        --data-binary @"$payload_file" 2>/dev/null)

    local body
    if [ "$http_code" = "200" ]; then
        body=$(parse_sse_to_json < "$sse_file")
    else
        body=$(cat "$sse_file")
    fi
    rm -f "$sse_file" 2>/dev/null || true
    printf '%s\n%s' "$body" "$http_code"
}

# 拆分 curl 响应为 http_code + body，结果写入全局变量
HTTP_CODE="" ; HTTP_BODY=""
split_http_response() {
    HTTP_CODE=$(tail -n1 <<< "$1")
    HTTP_BODY=$(head -n -1 <<< "$1")
}

# 通用 JSON 查询调度器
# 用法: json_query "$input" "jq_flags" "jq_expr" "py_script" "perl_script"
json_query() {
    local input="$1" jq_flags="$2" jq_expr="$3" py_script="$4" perl_script="$5"
    case "$JSON_BACKEND" in
        jq)     printf '%s' "$input" | jq $jq_flags "$jq_expr" 2>/dev/null || true ;;
        python) printf '%s' "$input" | "$PY_CMD" -c "$py_script" 2>/dev/null || true ;;
        perl)   printf '%s' "$input" | perl -MJSON::PP -0777 -ne "$perl_script" 2>/dev/null || true ;;
    esac
}

extract_tool_calls_json() {
    local out
    out=$(json_query "$1" "-c" \
        '(.choices[0].message.tool_calls // empty)' \
        'import json,sys
try: data=json.load(sys.stdin)
except: sys.exit(0)
tc=((data.get("choices") or [{}])[0] or {}).get("message",{}).get("tool_calls") or []
if tc: sys.stdout.write(json.dumps(tc,separators=(",",":")))' \
        'my $d; eval{$d=decode_json($_);1} or exit 0; my $c=$d->{choices}||[];
my $tc=(@$c?($c->[0]{message}||{})->{tool_calls}||[]:[]);
print encode_json($tc) if ref($tc) eq "ARRAY"&&@$tc;')
    [ "$out" = "[]" ] && out=""
    printf '%s' "$out"
}

extract_tool_call_records() {
    [ -z "$1" ] && return 0
    json_query "$1" "-c" \
        '.[]? | {id:(.id//""),name:(.function.name//""),command:(try(.function.arguments|fromjson|.command)catch""),args_raw:(.function.arguments//"")}' \
        'import json,sys
try: calls=json.load(sys.stdin)
except: sys.exit(0)
for c in calls or []:
 fn=c.get("function") or {};ar=fn.get("arguments","") or "";cmd=""
 try:
  a=json.loads(ar) if ar else {};cmd=a.get("command","") if isinstance(a,dict) else ""
 except: cmd=""
 sys.stdout.write(json.dumps({"id":c.get("id",""),"name":fn.get("name",""),"command":cmd,"args_raw":ar},separators=(",",":"))+"\n")' \
        'my $calls;eval{$calls=decode_json($_);1} or exit 0;
for my $c(@{$calls||[]}){my $fn=$c->{function}||{};my $ar=$fn->{arguments}//q{};my $cmd=q{};
if($ar ne q{}){my $a;eval{$a=decode_json($ar);1};$cmd=$a->{command}//q{} if ref($a) eq "HASH"}
print encode_json({id=>$c->{id}//q{},name=>$fn->{name}//q{},command=>$cmd,args_raw=>$ar}),"\n"}'
}

TOOL_RECORD_ID=""
TOOL_RECORD_NAME=""
TOOL_RECORD_COMMAND=""
TOOL_RECORD_ARGS_RAW=""

parse_tool_call_record() {
    local record_json="$1"
    TOOL_RECORD_ID="" ; TOOL_RECORD_NAME="" ; TOOL_RECORD_COMMAND="" ; TOOL_RECORD_ARGS_RAW=""
    local -a fields=()
    mapfile -d '' -t fields < <(
        json_query "$record_json" "-j" \
            '(.id//""),"\u0000",(.name//""),"\u0000",(.command//""),"\u0000",(.args_raw//""),"\u0000"' \
            'import json,sys
try: r=json.load(sys.stdin)
except: sys.exit(0)
for k in ("id","name","command","args_raw"):
 v=r.get(k,"")
 if not isinstance(v,str): v=""
 sys.stdout.buffer.write(v.encode("utf-8","replace")+b"\0")' \
            'my $r;eval{$r=decode_json($_);1} or exit 0;
for my $k(qw(id name command args_raw)){my $v=$r->{$k};$v=q{} if !defined $v||ref($v);print $v,"\0"}'
    )
    [ "${#fields[@]}" -ge 4 ] || return 1
    TOOL_RECORD_ID="${fields[0]}" ; TOOL_RECORD_NAME="${fields[1]}"
    TOOL_RECORD_COMMAND="${fields[2]}" ; TOOL_RECORD_ARGS_RAW="${fields[3]}"
}

# 从 OpenAI 兼容 JSON 响应中提取 assistant content
extract_ai_content() {
    local out
    out=$(json_query "$1" "-r" \
        '(.choices[0].message.content // .choices[0].text // empty)' \
        'import json,sys
try: data=json.load(sys.stdin)
except: sys.exit(0)
c=(data.get("choices") or [{}])[0] or {}
content=(c.get("message") or {}).get("content") or c.get("text") or ""
if isinstance(content,str): sys.stdout.write(content)' \
        'my $d; eval{$d=decode_json($_);1} or exit 0; my $c=$d->{choices}||[];
if(@$c){my $m=($c->[0]||{})->{message}||{};
print $m->{content}//$c->[0]{text}//"" }')
    [ "$out" = "null" ] && out=""
    if [ -n "$out" ]; then printf '%s' "$out"; return 0; fi
    # 正则回退
    out=$(printf '%s' "$1" | sed -n 's/.*"content"[[:space:]]*:[[:space:]]*"\([^"\\]*\(\\"[^"\\]*\)*\)".*/\1/p' | head -n1 | sed 's/\\n/\n/g;s/\\"/"/g;s/\\\\/\\/g' || true)
    printf '%s' "$out"
}

response_has_visible_output() {
    [ -n "$(extract_ai_content "$1")" ] || [ -n "$(extract_tool_calls_json "$1")" ]
}

extract_error_message() {
    local out
    out=$(json_query "$1" "-r" \
        '(.error.message // .message // .msg // .error // empty)' \
        'import json,sys
try: data=json.load(sys.stdin)
except: sys.exit(0)
err=data.get("error") or {}
msg=(err.get("message") if isinstance(err,dict) else None) or data.get("message") or data.get("msg") or data.get("error") or ""
if isinstance(msg,str): sys.stdout.write(msg)' \
        'my $d; eval{$d=decode_json($_);1} or exit 0; my $msg="";
if(ref($d->{error}) eq "HASH"&&defined $d->{error}{message}){$msg=$d->{error}{message}}
elsif(defined $d->{message}){$msg=$d->{message}}
elsif(defined $d->{msg}){$msg=$d->{msg}}
elsif(defined $d->{error}&&!ref($d->{error})){$msg=$d->{error}}
print $msg if defined $msg;')
    [ "$out" = "null" ] && out=""
    if [ -n "$out" ]; then printf '%s' "$out"; return 0; fi
    # 正则回退
    printf '%s' "$1" | grep -o '"message":"[^"]*"' | head -1 | sed 's/"message":"//;s/"$//' || true
}

# 调用 AI API，返回原始响应 body（非流式）
# 响应内容写入全局变量 AI_RAW_RESPONSE_BODY，避免命令替换触发子 shell 丢失状态。
call_ai_raw_response() {
    if ! [[ "$MAX_TOKENS" =~ ^[0-9]+$ ]]; then
        MAX_TOKENS=1024
    fi
    if ! [[ "$TEMPERATURE" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
        TEMPERATURE="0.7"
    fi

    local messages
    messages=$(build_messages_json)

    local payload
    payload=$(build_request_payload "$messages")

    local payload_file="$TMP_DIR/ai_install_payload_$$.json"
    printf '%s' "$payload" > "$payload_file"

    if [ -n "$LOG_FILE" ] && [ "$DEBUG_LOG" = "1" ]; then
        log_line "DEBUG" "AI request: url=$API_URL/chat/completions model=$MODEL_NAME payload=$(redact_secrets "$(cat "$payload_file")")"
    fi

    local response
    response=$(post_chat_completion_payload "$payload_file")
    rm -f "$payload_file" 2>/dev/null || true
    split_http_response "$response"
    local http_code="$HTTP_CODE" body="$HTTP_BODY"

    # HTTP 400 时尝试简化参数重试
    if [ "$http_code" = "400" ]; then
        local fb_file="$TMP_DIR/ai_install_payload_fallback_$$.json"
        printf '{"model":"%s","messages":%s,"max_tokens":512,"stream":true,"tools":%s,"tool_choice":"auto"}' \
            "$MODEL_NAME" "$messages" "$(get_shell_tool_definitions_json)" > "$fb_file"
        [ "$DEBUG_LOG" = "1" ] && log_line "DEBUG" "AI fallback request (no temperature, max_tokens=512)"
        local fb_response
        fb_response=$(post_chat_completion_payload "$fb_file")
        rm -f "$fb_file" 2>/dev/null || true
        split_http_response "$fb_response"
        [ "$DEBUG_LOG" = "1" ] && log_line "DEBUG" "AI fallback response: http_code=$HTTP_CODE"
        if [ "$HTTP_CODE" = "200" ]; then
            http_code="$HTTP_CODE" ; body="$HTTP_BODY"
        fi
    fi

    if [ "$http_code" != "200" ]; then
        print_color "$RED" "[错误] AI 调用失败" >&2
        local error_msg=""
        error_msg=$(extract_error_message "$body")
        if [ -n "$error_msg" ]; then
            print_color "$YELLOW" "错误信息: $error_msg" >&2
        else
            print_color "$YELLOW" "HTTP 状态码: $http_code" >&2
        fi
        log_line "ERROR" "AI call failed: http_code=$http_code url=$API_URL/chat/completions model=$MODEL_NAME body=$(echo "$body" | tr '\n' ' ')"
        print_color "$CYAN" "日志已写入: $LOG_FILE" >&2
        if [ "$DEBUG_LOG" != "1" ]; then
            print_color "$CYAN" "可用 AI_INSTALL_DEBUG=1 重新运行以记录请求 payload（已脱敏）" >&2
        fi
        return 1
    fi

    AI_RAW_RESPONSE_BODY="$body"
    return 0
}

render_ai_message() {
    local message="$1"
    [ -z "$message" ] && return 0
    print_color "$BLUE" "\nAI: $message\n" >&2
}

strip_think_blocks() {
    local text="$1"
    if (( HAS_PERL )); then
        printf '%s' "$text" | perl -0777 -pe 's/<think>.*?<\/think>//gs'
    else
        printf '%s' "$text" | sed '/<think>/,/<\/think>/d'
    fi
}

execute_tool_calls_from_json() {
    local records
    records=$(extract_tool_call_records "$1")
    if [ -z "$records" ]; then
        print_color "$YELLOW" "[警告] tool_calls 存在，但未能解析出可执行工具。" >&2
        return 1
    fi

    local record=""
    while IFS= read -r record; do
        [ -z "$record" ] && continue
        if ! parse_tool_call_record "$record"; then
            append_tool_result_to_history "" "错误：工具调用记录解析失败。原始记录：$record"
            continue
        fi

        local tool_output=""
        if [ "$TOOL_RECORD_NAME" != "run_shell_command" ]; then
            tool_output="错误：不支持的工具 $TOOL_RECORD_NAME"
        elif [ -z "$TOOL_RECORD_COMMAND" ]; then
            tool_output="错误：工具参数缺少 command 字段。原始参数：$TOOL_RECORD_ARGS_RAW"
        elif ! command_needs_confirmation "$TOOL_RECORD_COMMAND" || confirm_command "$TOOL_RECORD_COMMAND"; then
            tool_output=$(execute_command "$TOOL_RECORD_COMMAND")
        elif [ -n "$USER_REJECT_FEEDBACK" ]; then
            tool_output="已跳过：用户拒绝执行该命令。用户反馈：$USER_REJECT_FEEDBACK"
        else
            tool_output="已跳过：用户未同意执行该命令。"
        fi
        append_tool_result_to_history "$TOOL_RECORD_ID" "$tool_output"
    done <<< "$records"
    return 0
}

request_ai_response() {
    local user_message="$1"
    append_user_message_to_history "$user_message"

    local round=0
    while [ "$round" -lt "$MAX_TOOL_ROUNDS" ]; do
        if ! call_ai_raw_response; then
            return 1
        fi
        local body="$AI_RAW_RESPONSE_BODY"

        local ai_message=""
        ai_message=$(extract_ai_content "$body")
        ai_message=$(strip_think_blocks "$ai_message")
        local tool_calls_json=""
        tool_calls_json=$(extract_tool_calls_json "$body")

        if [ -n "$tool_calls_json" ]; then
            append_assistant_tool_calls_to_history "$ai_message" "$tool_calls_json"
            render_ai_message "$ai_message"
            execute_tool_calls_from_json "$tool_calls_json" || true
            round=$((round + 1))
            continue
        fi

        if [ -z "$ai_message" ]; then
            print_color "$RED" "[错误] AI 调用失败" >&2
            log_line "ERROR" "AI call returned empty content without tool_calls: body=$(echo "$body" | tr '\n' ' ')"
            print_color "$YELLOW" "原因: 接口返回了空消息（content=null），不是终端没显示。" >&2
            print_color "$CYAN" "日志已写入: $LOG_FILE" >&2
            return 1
        fi

        append_assistant_text_to_history "$ai_message"
        printf '%s' "$ai_message"
        return 0
    done

    print_color "$RED" "[错误] AI 工具调用次数过多，已中止当前轮对话" >&2
    log_line "ERROR" "AI exceeded max tool rounds: $MAX_TOOL_ROUNDS"
    return 1
}

# 执行命令
execute_command() {
    local command="$1"
    
    # 使用高对比颜色显示命令，便于在终端里区分
    echo "" >&2
    echo -e "\033[44;37m 执行命令 \033[0m \033[1;36m$command\033[0m" >&2
    echo -e "\033[0;90m----------------------------------------\033[0m" >&2
    
    # 执行命令并捕获输出（同时显示在终端）
    local output
    local exit_code
    local temp_log="$TMP_DIR/ai_install_cmd_output_$$.log"
    local errexit_was_set=0
    local pipefail_was_set=0

    case "$-" in
        *e*) errexit_was_set=1 ;;
    esac
    if set -o | grep -q '^pipefail[[:space:]]*on$'; then
        pipefail_was_set=1
    fi
    
    # 使用 tee 实时显示输出并保存到临时文件
    # set -o pipefail 确保管道中任何命令失败都返回失败
    set +e
    set -o pipefail
    eval "$command" 2>&1 | tee "$temp_log" >&2
    exit_code=${PIPESTATUS[0]}
    (( pipefail_was_set )) || set +o pipefail
    (( errexit_was_set )) && set -e
    
    # 读取捕获的输出
    output=$(cat "$temp_log")
    rm -f "$temp_log"
    
    echo -e "\033[0;90m----------------------------------------\033[0m" >&2
    if [ $exit_code -eq 0 ]; then
        print_color "$GREEN" "[OK] 执行成功" >&2
    else
        print_color "$RED" "[错误] 执行失败 (返回码: $exit_code)" >&2
    fi
    echo "" >&2
    
    # 将结果反馈给 AI（去除 ANSI 颜色代码，防止 JSON 错误）
    local clean_output
    clean_output=$(redact_secrets "$(sanitize_text "$output")")
    local status="失败"
    [ $exit_code -eq 0 ] && status="成功"
    local feedback=$'命令执行'"$status"$'。\n输出:\n'"$clean_output"
    
    printf '%s' "$feedback"
}

# 主对话循环
chat_loop() {
    print_header "开始对话（输入 quit 或 exit 退出）"
    
    # 初始问候
    print_color "$YELLOW" "正在初始化 AI 助手..."
    local initial_response=""
    if initial_response=$(request_ai_response "请先简单打个招呼，并询问用户需要什么帮助。"); then
        render_ai_message "$initial_response"
    fi
    
    while true; do
        printf '\n'
        read -r -p "$(prompt_color "$GREEN" "你: ")" user_input
        
        # 检查退出命令
        if [[ "$user_input" =~ ^(quit|exit|退出|q)$ ]]; then
            print_color "$CYAN" "\n再见！"
            break
        fi
        
        # 跳过空输入
        if [ -z "$user_input" ]; then
            continue
        fi
        
        # 调用 AI
        local ai_response=""
        if ai_response=$(request_ai_response "$user_input"); then
            render_ai_message "$ai_response"
        fi
    done
}

# 主函数
main() {
    detect_json_backend
    init_log
    log_line "INFO" "ai_install started (cwd=$(pwd))"

    # 检查 curl 是否安装
    if ! command -v curl &> /dev/null; then
        print_color "$RED" "[错误] 未找到 curl 命令，请先安装 curl"
        echo "Ubuntu/Debian: sudo apt-get install curl"
        echo "CentOS/RHEL: sudo yum install curl"
        echo "macOS: brew install curl"
        exit 1
    fi
    
    # 构建系统提示词
    build_system_prompt
    
    # 配置 API
    if ! setup_api; then
        print_color "$CYAN" "退出程序"
        exit 1
    fi
    log_line "INFO" "config: api_url=$API_URL model=$MODEL_NAME max_tokens=$MAX_TOKENS temperature=$TEMPERATURE include_temperature=$INCLUDE_TEMPERATURE log_file=$LOG_FILE"
    
    # 开始对话
    chat_loop
}

# 运行主函数（被 source 时不自动执行）
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main
fi
