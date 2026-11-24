/** 统一请求入口：视图只依赖本模块，fetch 细节收敛在 http.ts。 */
export { ApiError, api } from './http'