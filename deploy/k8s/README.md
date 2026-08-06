# 部署到 Kubernetes

namespace `calton`。清单按顺序 apply,MySQL 版与 SQLite 版二选一。

## 前置(按环境填占位符)
- 容器镜像仓库:`REGISTRY`(clusters/nodes 需能拉取;若非匿名,配 imagePullSecret)
- 构建镜像:`docker build -f server/Dockerfile -t REGISTRY/calton:TAG .`(构建上下文是仓库根)
- MySQL(如用 MySQL 版):主机填 `MYSQL_HOST`,库 `calton`,账号见 Secret

## 步骤
```bash
kubectl apply -f 00-namespace.yaml
# 密钥现场生成,不要提交真实值:
kubectl -n calton create secret generic calton-secrets \
  --from-literal=CALTON_SERVICE_SECRET="$(openssl rand -hex 32)" \
  --from-literal=CALTON_DATABASE_PASSWORD="<db-password>"      # MySQL 版才需要
# SQLite 版:
kubectl apply -f 20-pvc.yaml -f 30-deployment.yaml -f 40-service.yaml
# 或 MySQL 版:
kubectl apply -f 25-db-configmap.yaml -f 30-deployment.mysql.yaml -f 40-service.yaml
kubectl -n calton rollout status deploy/calton
```

## 关键点
- **迁移不自动跑**:应用启动不执行 `alembic upgrade`,靠 initContainer(见 deployment)。
- **SQLite ⇒ 副本 1 且 `strategy: Recreate`**:单文件库多写会损坏,RWO 卷配滚动更新会卡 Pending。MySQL 版 DB 外置,但附件仍在 PVC,故也暂为单副本。
- **`CALTON_SERVICE_TESTINGTOKEN` 生产绝不能设**:会挂上重置数据库的测试路由。
- **`CALTON_SERVICE_SECRET` 是 JWT 签名密钥**:现场生成、放 Secret;变了所有 token 失效。
- 环境变量前缀以镜像内 config 为准(`CALTON_*`)。

## 环境变量
| 变量 | 说明 |
|---|---|
| `CALTON_DATABASE_TYPE` | `sqlite`(默认)或 `mysql` |
| `CALTON_DATABASE_PATH` | sqlite:库文件路径(PVC 内) |
| `CALTON_DATABASE_HOST/PORT/USER/PASSWORD/DATABASE` | mysql 连接参数 |
| `CALTON_FILES_BASEPATH` | 附件落盘路径 |
| `CALTON_SERVICE_SECRET` | JWT 密钥(必填,来自 Secret) |
