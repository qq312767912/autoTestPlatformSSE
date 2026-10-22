import type { SkillListItem } from '../skills/types'
import type { SkillVersionSummary } from '../skills/types/hub'

export interface ReviewVersionOption {
  id: string
  skillId: number
  version: string
  title: string
  description: string
  label: string
  disabled: boolean
  unavailableReason: string
}

export function buildReviewVersionOptions(skill: Pick<SkillListItem, 'id' | 'name' | 'display_name'>, versions: SkillVersionSummary[]): ReviewVersionOption[] {
  return versions.map(version => {
    const unavailableReason = version.archived ? '已删除' : version.download_available === false ? '包文件缺失'
      : version.state === 'quarantined' ? '已隔离' : version.state === 'rejected' ? '校验未通过' : ''
    const title = `${skill.display_name || skill.name} · v${version.version}`
    const description = version.change_reason?.trim() || '未填写变更描述'
    return {id: version.id, skillId: skill.id, version: version.version, title, description,
      label: `${title} · ${description}${unavailableReason ? `（${unavailableReason}）` : ''}`,
      disabled: Boolean(unavailableReason), unavailableReason}
  })
}
