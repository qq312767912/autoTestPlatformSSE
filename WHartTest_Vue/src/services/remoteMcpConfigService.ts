import { request } from '@/utils/request';

export interface RemoteMcpConfig {
  id?: number;
  name: string;
  url: string;
  transport: 'stdio' | 'streamable_http' | 'sse';
  headers?: Record<string, string>;
  is_active: boolean;
  created_at?: string;
  updated_at?: string;
}

interface ApiResponse<T> {
  status: string;
  code: number;
  message: string;
  data: T;
  errors: any;
}

interface PingResponse {
  success: boolean;
  message: string;
  response_time?: number;
}

export interface VisionModelConfig {
  id?: number;
  name: string;
  base_url: string;
  chat_completions_path: string;
  model: string;
  api_key?: string;
  has_api_key?: boolean;
  timeout_seconds: number;
  max_retries: number;
  is_active: boolean;
  updated_at?: string;
}

export const fetchVisionModelConfig = async (): Promise<VisionModelConfig | null> => {
  const response = await request<VisionModelConfig[]>({
    url: '/mcp_tools/vision-model-configs/', method: 'GET'
  });
  if (!response.success) throw new Error(response.error || '获取视觉模型配置失败');
  const data: any = response.data;
  const rows = Array.isArray(data) ? data : (data?.results || []);
  return rows[0] || null;
};

export const saveVisionModelConfig = async (config: VisionModelConfig): Promise<VisionModelConfig> => {
  const response = await request<VisionModelConfig>({
    url: config.id
      ? `/mcp_tools/vision-model-configs/${config.id}/`
      : '/mcp_tools/vision-model-configs/',
    method: config.id ? 'PATCH' : 'POST',
    data: config
  });
  if (!response.success || !response.data) throw new Error(response.error || '保存视觉模型配置失败');
  return response.data;
};

export const testVisionModelConfig = async (config: VisionModelConfig): Promise<string> => {
  if (!config.id) throw new Error('请先保存配置后再测试连接');
  const response = await request<{ message?: string }>({
    url: `/mcp_tools/vision-model-configs/${config.id}/test-connection/`,
    method: 'POST',
    data: config
  });
  if (!response.success) throw new Error(response.error || '视觉模型连接失败');
  return (response.data as any)?.message || response.message || '视觉模型连接成功';
};

// 获取所有远程MCP配置
export const fetchRemoteMcpConfigs = async (): Promise<RemoteMcpConfig[]> => {
  try {
    const response = await request<RemoteMcpConfig[]>({
      url: '/mcp_tools/remote-configs/',
      method: 'GET'
    });

    if (response.success) {
      return response.data || [];
    } else {
      throw new Error(response.error || '获取远程MCP配置失败');
    }
  } catch (error) {
    console.error('获取远程MCP配置失败:', error);
    throw error;
  }
};

// 获取单个远程MCP配置
export const fetchRemoteMcpConfigById = async (id: number): Promise<RemoteMcpConfig> => {
  try {
    const response = await request<RemoteMcpConfig>({
      url: `/mcp_tools/remote-configs/${id}/`,
      method: 'GET'
    });

    if (response.success) {
      return response.data!;
    } else {
      throw new Error(response.error || `获取远程MCP配置(ID: ${id})失败`);
    }
  } catch (error) {
    console.error(`获取远程MCP配置(ID: ${id})失败:`, error);
    throw error;
  }
};

// 创建新的远程MCP配置
export const createRemoteMcpConfig = async (config: RemoteMcpConfig): Promise<RemoteMcpConfig> => {
  try {
    const response = await request<RemoteMcpConfig>({
      url: '/mcp_tools/remote-configs/',
      method: 'POST',
      data: config
    });

    if (response.success) {
      return response.data!;
    } else {
      throw new Error(response.error || '创建远程MCP配置失败');
    }
  } catch (error) {
    console.error('创建远程MCP配置失败:', error);
    throw error;
  }
};

// 更新远程MCP配置
export const updateRemoteMcpConfig = async (id: number, config: Partial<RemoteMcpConfig>): Promise<RemoteMcpConfig> => {
  try {
    const response = await request<RemoteMcpConfig>({
      url: `/mcp_tools/remote-configs/${id}/`,
      method: 'PATCH',
      data: config
    });

    if (response.success) {
      return response.data!;
    } else {
      throw new Error(response.error || `更新远程MCP配置(ID: ${id})失败`);
    }
  } catch (error) {
    console.error(`更新远程MCP配置(ID: ${id})失败:`, error);
    throw error;
  }
};

// 删除远程MCP配置
export const deleteRemoteMcpConfig = async (id: number): Promise<void> => {
  try {
    const response = await request<void>({
      url: `/mcp_tools/remote-configs/${id}/`,
      method: 'DELETE'
    });

    if (!response.success) {
      throw new Error(response.error || `删除远程MCP配置(ID: ${id})失败`);
    }
  } catch (error) {
    console.error(`删除远程MCP配置(ID: ${id})失败:`, error);
    throw error;
  }
};

// 测试远程MCP服务器连通性
export const pingRemoteMcpConfig = async (configId: number): Promise<PingResponse> => {
  try {
    const response = await request<any>({
      url: '/mcp_tools/remote-configs/ping/',
      method: 'POST',
      data: {
        config_id: configId
      }
    });

    if (response.success && response.data) {
      const pingResultPayload = response.data;
      // 根据ping结果的内部status判断是否成功
      const isSuccess = pingResultPayload && pingResultPayload.status === 'online';

      return {
        success: isSuccess,
        message: response.message || (isSuccess ? '连接成功' : '连接失败'),
        response_time: pingResultPayload?.response_time
      };
    } else {
      return {
        success: false,
        message: response.error || '连接失败'
      };
    }
  } catch (error) {
    console.error(`测试远程MCP配置(ID: ${configId})连通性失败:`, error);
    let errorMessage = '未知错误';
    if (error instanceof Error) {
      errorMessage = error.message;
    }
    return {
      success: false,
      message: errorMessage
    };
  }
};
