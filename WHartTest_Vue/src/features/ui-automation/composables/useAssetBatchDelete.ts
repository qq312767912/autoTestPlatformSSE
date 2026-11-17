import { computed, h, ref, watch, type Ref } from 'vue'
import { Message, Modal } from '@arco-design/web-vue'
import { useAuthStore } from '@/store/authStore'
import { extractResponseData } from '../types'

export function useAssetBatchDelete(options: {
  project: Readonly<Ref<number | undefined>>
  rows: Ref<Array<{id: number; name: string}>>
  permission: string
  label: string
  remove: (project: number, ids: number[]) => Promise<any>
  reload: () => Promise<void>
}) {
  const auth = useAuthStore()
  const selectedAssetIds = ref<number[]>([])
  const batchDeleting = ref(false)
  const canBatchDelete = computed(() => auth.hasPermission(options.permission))
  watch([options.project, options.rows], () => { selectedAssetIds.value = [] })
  function confirmBatchDelete() {
    const project = options.project.value
    const ids = [...selectedAssetIds.value]
    if (!project || !ids.length || batchDeleting.value) return
    if (ids.length > 100) return Message.warning('单次最多删除100条，请分批处理')
    Modal.confirm({
      title: `删除选中的 ${ids.length} 个${options.label}？`,
      content: options.label === '页面' ? '将删除勾选的页面及其未被引用的元素。存在页面步骤或元素被引用的页面将保留，并逐条说明原因。' : '将删除勾选的页面步骤及其操作、断言明细。被测试用例引用的页面步骤将保留，并逐条说明原因。',
      okText: '确认删除', cancelText: '保留数据',
      onOk: async () => {
        if (options.project.value !== project) { Message.warning('项目已切换，请重新选择'); return false }
        batchDeleting.value = true
        try {
          const response = await options.remove(project, ids)
          const result = extractResponseData<{deleted_count: number; blocked: Array<{id:number;name:string;reason:string}>}>(response)
          if (!result) throw new Error('删除结果无效，请刷新核对')
          await options.reload()
          selectedAssetIds.value = []
          if (result.blocked.length) {
            Modal.info({title:`已删除 ${result.deleted_count} 条，保留 ${result.blocked.length} 条`,
              content:()=>h('div',{style:'white-space:pre-wrap;max-height:320px;overflow:auto'}, result.blocked.map(x=>`${x.name}（ID ${x.id}）：${x.reason}`).join('\n'))})
          } else Message.success(`已删除 ${result.deleted_count} 个${options.label}`)
        } catch (error: any) {
          const body = error?.response?.data || error
          Message.error(body?.detail || body?.error || body?.message || '批量删除失败')
          return false
        } finally { batchDeleting.value = false }
      },
    })
  }
  return {selectedAssetIds, batchDeleting, canBatchDelete, confirmBatchDelete}
}
