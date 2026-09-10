<template>
  <div class="review-page">
    <section class="hero">
      <div>
        <div class="eyebrow">TEST CASE QUALITY</div>
        <h1>用例审查</h1>
        <p>上传测试用例，由 test-case-clarity-review Skill 检查可执行性、验收标准和覆盖缺口，并生成 Excel 报告。</p>
      </div>
      <a-button type="primary" size="large" @click="openCreate">发起审查</a-button>
    </section>

    <a-alert v-if="!projectId" type="warning">请先在顶部选择项目。</a-alert>
    <section v-else class="content-card">
      <div class="section-head">
        <div><h2>审查记录</h2><span>报告按项目共享，原始文件不会被修改</span></div>
        <a-button @click="load">刷新</a-button>
      </div>
      <a-spin :loading="loading" style="width: 100%">
        <a-empty v-if="!loading && !reviews.length" description="还没有用例审查记录" />
        <div v-else class="review-list">
          <article v-for="item in reviews" :key="item.id" class="review-item">
            <div class="file-mark">XLS</div>
            <div class="review-main">
              <div class="review-title-row">
                <strong>{{ item.source_name }}</strong>
                <a-tag :color="statusMeta(item.status).color">{{ statusMeta(item.status).label }}</a-tag>
              </div>
              <div class="meta">{{ item.creator_name || '未知用户' }} · {{ formatTime(item.created_at) }}</div>
              <div class="skill-line">
                <span>{{ item.review_mode === 'general' ? '通用审查' : '指定 Skill' }}</span>
                <b>{{ item.skill_name }}</b>
              </div>
              <a-progress v-if="item.status === 'pending' || item.status === 'running'" :percent="item.progress / 100" size="small" />
              <div class="step">{{ item.current_step }}</div>
              <div v-if="item.status === 'completed'" class="summary">
                <span>扫描 {{ item.summary?.total_rows || 0 }} 行</span>
                <span class="danger">高风险 {{ item.summary?.high || 0 }}</span>
                <span class="warning">中风险 {{ item.summary?.medium || 0 }}</span>
                <span>低风险 {{ item.summary?.low || 0 }}</span>
              </div>
              <a-alert v-if="item.status === 'failed'" type="error">{{ item.error_message || '审查失败' }}</a-alert>
            </div>
            <div class="actions">
              <a-button v-if="item.report_url" type="primary" @click="download(item.report_url)">下载报告</a-button>
              <a-button v-if="item.status === 'failed'" @click="retry(item)">重试</a-button>
              <a-popconfirm content="删除记录、源文件和报告？" @ok="remove(item)">
                <a-button status="danger" type="text">删除</a-button>
              </a-popconfirm>
            </div>
          </article>
        </div>
      </a-spin>
    </section>

    <a-modal v-model:visible="createVisible" :width="720" :ok-loading="submitting" ok-text="开始审查" @ok="submit">
      <template #title>
        <div class="modal-title"><span class="modal-title-mark">✓</span><span>发起用例审查</span></div>
      </template>
      <div class="review-form-intro">
        <strong>创建一次独立的质量审查</strong>
        <span>上传用例并选择审查方式，完成后可下载 Excel 审查报告。</span>
      </div>
      <a-form class="review-form" layout="vertical">
        <section class="form-section">
          <div class="form-section-title"><span>01</span><div><strong>选择审查方式</strong><small>决定本次任务使用的审查能力</small></div></div>
          <a-form-item label="审查方式" required>
            <div class="field-stack">
              <a-radio-group v-model="reviewMode" type="button" class="review-mode-group">
                <a-radio value="general">通用审查（内置 Skill）</a-radio>
                <a-radio value="specified">指定 Skill 审查</a-radio>
              </a-radio-group>
              <div class="hint">通用审查固定使用 test-case-clarity-review，适合常规测试用例质量检查。</div>
            </div>
          </a-form-item>
          <a-form-item v-if="reviewMode === 'specified'" label="审查 Skill" required>
            <a-select v-model="selectedSkillId" allow-search placeholder="选择一个已启用的 Skill">
              <a-option v-for="skill in availableSkills" :key="skill.id" :value="skill.id">
                {{ skill.name }} · {{ skill.description }}
              </a-option>
            </a-select>
          </a-form-item>
        </section>

        <section class="form-section">
          <div class="form-section-title"><span>02</span><div><strong>上传测试用例</strong><small>系统只读取文件，不会修改原始内容</small></div></div>
          <a-form-item label="测试用例文件" required>
            <div class="field-stack">
              <a-upload
                class="review-upload"
                :file-list="fileList"
                :auto-upload="false"
                :show-retry-button="false"
                :limit="1"
                accept=".xlsx,.csv"
                draggable
                @change="onFileChange"
              />
              <div class="hint">支持 XLSX、CSV，单个文件最大 50MB。</div>
            </div>
          </a-form-item>
        </section>

        <section class="form-section">
          <div class="form-section-title"><span>03</span><div><strong>补充审查信息</strong><small>内容越明确，审查结论越贴近实际业务</small></div></div>
          <a-form-item label="业务背景（可选）">
            <a-textarea v-model="businessContext" :max-length="5000" show-word-limit :auto-size="{ minRows: 4, maxRows: 8 }"
              placeholder="例如：订单取消仅允许待支付状态；重点检查金额、权限和状态流转。未知规则可留空，报告会标记为待业务确认。" />
          </a-form-item>
          <a-form-item label="本次审查规则（可选）">
            <div class="field-stack">
              <a-textarea v-model="customRules" :max-length="5000" show-word-limit :auto-size="{ minRows: 4, maxRows: 8 }"
                placeholder="例如：导出类用例必须校验表头、行数和关键字段；权限缺失统一标为高风险。" />
              <div class="hint">规则仅作用于本次任务，并随报告保存，方便后续追溯。</div>
            </div>
          </a-form-item>
        </section>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import { useProjectStore } from '@/store/projectStore';
import { createReview, deleteReview, listReviews, retryReview, type TestCaseReview } from './service';
import { SkillService } from '@/features/skills/services/skillService';

const projectStore = useProjectStore();
const projectId = computed(() => projectStore.currentProjectId);
const reviews = ref<TestCaseReview[]>([]);
const loading = ref(false);
const submitting = ref(false);
const createVisible = ref(false);
const selectedFile = ref<File | null>(null);
const fileList = ref<any[]>([]);
const businessContext = ref('');
const reviewMode = ref<'general' | 'specified'>('general');
const selectedSkillId = ref<number>();
const customRules = ref('');
const availableSkills = ref<any[]>([]);
let timer: number | undefined;

const statusMeta = (status: TestCaseReview['status']) => ({
  pending: { label: '等待中', color: 'gray' }, running: { label: '审查中', color: 'blue' },
  completed: { label: '已完成', color: 'green' }, failed: { label: '失败', color: 'red' },
}[status]);
const formatTime = (value: string) => value ? new Date(value).toLocaleString() : '-';

async function load(silent = false) {
  if (!projectId.value) return;
  if (!silent) loading.value = true;
  try {
    reviews.value = await listReviews(projectId.value);
    availableSkills.value = (await SkillService.getSkills(projectId.value)).filter(skill => skill.is_active);
  }
  catch (error: any) { if (!silent) Message.error(error?.message || '加载审查记录失败'); }
  finally { loading.value = false; }
}
function openCreate() { if (!projectId.value) return Message.warning('请先选择项目'); createVisible.value = true; }
function onFileChange(files: any[]) {
  fileList.value = files;
  selectedFile.value = files?.[0]?.file || null;
}
async function submit() {
  if (!projectId.value || !selectedFile.value) return Message.warning('请选择测试用例文件');
  if (reviewMode.value === 'specified' && !selectedSkillId.value) return Message.warning('请选择用于审查的 Skill');
  submitting.value = true;
  try {
    await createReview(projectId.value, selectedFile.value, {
      businessContext: businessContext.value.trim(),
      reviewMode: reviewMode.value,
      selectedSkill: selectedSkillId.value,
      customRules: customRules.value.trim(),
    });
    Message.success('审查任务已创建'); createVisible.value = false; selectedFile.value = null; fileList.value = [];
    businessContext.value = ''; customRules.value = ''; reviewMode.value = 'general'; selectedSkillId.value = undefined; await load();
  } catch (error: any) { Message.error(error?.response?.data?.source_file?.[0] || error?.message || '创建失败'); }
  finally { submitting.value = false; }
}
async function retry(item: TestCaseReview) { if (!projectId.value) return; await retryReview(projectId.value, item.id); Message.success('已重新提交'); await load(); }
async function remove(item: TestCaseReview) { if (!projectId.value) return; await deleteReview(projectId.value, item.id); Message.success('已删除'); await load(); }
function download(url: string) { const link = document.createElement('a'); link.href = url; link.download = ''; document.body.appendChild(link); link.click(); link.remove(); }

watch(projectId, () => load());
onMounted(() => { load(); timer = window.setInterval(() => { if (reviews.value.some(item => ['pending', 'running'].includes(item.status))) load(true); }, 4000); });
onBeforeUnmount(() => { if (timer) window.clearInterval(timer); });
</script>

<style scoped>
.review-page{padding:24px;min-height:100%;background:#f5f7fa;color:#1d2939}.hero{display:flex;justify-content:space-between;align-items:flex-end;padding:34px 38px;margin-bottom:20px;border-radius:16px;color:white;background:linear-gradient(120deg,#15395b,#0f766e);box-shadow:0 12px 30px rgba(21,57,91,.16)}.eyebrow{font-size:12px;letter-spacing:2px;color:#99f6e4}.hero h1{font-size:30px;margin:8px 0}.hero p{margin:0;max-width:760px;color:#d8edf0;line-height:1.7}.content-card{background:#fff;border:1px solid #e5e9f0;border-radius:14px;padding:24px}.section-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}.section-head h2{margin:0 0 5px;font-size:20px}.section-head span,.meta,.step,.hint{font-size:13px;color:#8492a6}.review-list{display:grid;gap:12px}.review-item{display:flex;gap:16px;align-items:flex-start;padding:20px;border:1px solid #e8edf3;border-radius:12px;transition:.2s}.review-item:hover{border-color:#9dd8d2;box-shadow:0 5px 18px rgba(15,118,110,.08)}.file-mark{flex:none;width:48px;height:48px;border-radius:10px;display:grid;place-items:center;background:#e8f7f4;color:#0f766e;font-weight:700}.review-main{min-width:0;flex:1}.review-title-row{display:flex;gap:10px;align-items:center}.review-title-row strong{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.meta{margin:6px 0}.skill-line{display:flex;gap:8px;align-items:center;margin:7px 0;font-size:12px}.skill-line span{padding:2px 8px;border-radius:99px;background:#edf7f5;color:#0f766e}.skill-line b{font-weight:500;color:#526173}.step{margin-top:5px}.summary{display:flex;gap:16px;margin-top:10px;font-size:13px}.danger{color:#d4380d}.warning{color:#d46b08}.actions{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.modal-title{display:flex;align-items:center;justify-content:center;gap:9px;font-size:17px}.modal-title-mark{display:grid;place-items:center;width:24px;height:24px;border-radius:8px;background:#e8f7f4;color:#0f766e;font-size:14px;font-weight:800}.review-form-intro{display:flex;flex-direction:column;gap:5px;margin:-4px 0 18px;padding:15px 17px;border:1px solid #dcece8;border-radius:10px;background:linear-gradient(120deg,#f3faf8,#f8fbff)}.review-form-intro strong{font-size:15px;color:#234657}.review-form-intro span{font-size:13px;color:#718096}.review-form{display:grid;gap:14px}.form-section{padding:18px 20px 5px;border:1px solid #e5eaf0;border-radius:12px;background:#fff}.form-section-title{display:flex;align-items:center;gap:11px;margin-bottom:16px}.form-section-title>span{display:grid;place-items:center;width:32px;height:32px;border-radius:9px;background:#15395b;color:#fff;font-size:11px;font-weight:700;letter-spacing:.5px}.form-section-title>div{display:flex;flex-direction:column;gap:2px}.form-section-title strong{font-size:15px;color:#253748}.form-section-title small{font-size:12px;color:#96a2b2}.field-stack{display:flex;flex-direction:column;width:100%;gap:8px}.review-mode-group{display:grid!important;grid-template-columns:1fr 1fr;width:100%}.review-mode-group :deep(.arco-radio-button){display:flex;justify-content:center}.review-upload{display:block;width:100%}.review-upload :deep(.arco-upload){width:100%}.review-upload :deep(.arco-upload-drag){width:100%;min-height:112px;border-radius:10px;background:#f8fafc;border-color:#cad6e2;transition:.2s}.review-upload :deep(.arco-upload-drag:hover){border-color:#0f8f82;background:#f3faf8}.review-form :deep(.arco-form-item){margin-bottom:16px}.review-form :deep(.arco-form-item-content){width:100%}.review-form :deep(.arco-textarea-wrapper){border-radius:8px;background:#f8fafc}.review-form :deep(.arco-select-view){border-radius:8px;background:#f8fafc}
@media(max-width:760px){.hero,.review-item{align-items:stretch;flex-direction:column}.actions{justify-content:flex-end}.form-section{padding:15px 14px 2px}.review-mode-group{grid-template-columns:1fr}.review-form-intro{margin-top:0}}
</style>
