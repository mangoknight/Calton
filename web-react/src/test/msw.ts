import { setupServer } from 'msw/node';

import { defaultHandlers } from './handlers';

/**
 * 全局 MSW server。开发与单测都靠它顶住后端未就绪的阶段。
 * 用例里用 server.use(...) 覆盖单个端点，afterEach 自动恢复默认 handler。
 */
export const server = setupServer(...defaultHandlers);
