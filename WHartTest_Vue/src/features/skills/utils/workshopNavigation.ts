/** Hub 与业务证据进入同一工坊；路由只定位，版本/会话仍由服务端校验。 */
export function skillWorkshopLocation(projectId:number,skillId:number,versionId:string,assessmentId='') {
  return {path:'/knowledge-evolution',query:{view:'data',tab:'skills',project:String(projectId),skill_id:String(skillId),skill_version_id:versionId,...(assessmentId?{assessment_id:assessmentId}:{})}}
}
