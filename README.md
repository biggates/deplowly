# deplowly

[![GitHub](https://img.shields.io/badge/GitHub-biggates%2Fdeplowly-blue?logo=github)](https://github.com/biggates/deplowly)

## 名字的由来

**deplowly** = **depl**oy + **owl** + **y**

- **depl** — 来自 *deploy*，这是它的核心使命：把新镜像部署上去
- **owl** — 猫头鹰，象征它像猫头鹰一样「不睡觉地盯着」镜像仓库
- **y** — Python 写的小工具后缀，也呼应它的轻巧

所以 deplowly 是一只「盯着部署的猫头鹰」🦉，专门替你盯着集群里的镜像有没有换新。

## 它是干什么的

简单说：当镜像仓库推了新的镜像，它就自动帮你把服务重启一下，让新版本生效。

很多人在开发模式下会这样配置 Kubernetes：集群里有好几个 `Deployment`，它们的 `imagePullPolicy` 设成了 `always`。这意味着只要 Pod 被重新拉起，就会去仓库拉最新的镜像。

但问题来了 —— 你推了新镜像，Deployment 不会自己重启，正常情况下你得手动 `kubectl rollout restart deployment/xxx` 一下。

deplowly 就是把这个「手动重启」的过程自动化了的小工具。

它会周期性地检查你指定的 Deployment 对应的镜像在远端仓库里有没有变化（比对 `Docker-Content-Digest`，不靠 tag 名字）。一旦发现镜像内容变了，就自动对该 Deployment 执行 `rollout restart`，（通过打上 `kubectl.kubernetes.io/restartedAt` 注解），让 K8s 自己去拉去新的镜像。

> 一句话：**开发模式镜像自动更新兜底工具** —— 推完镜像不用再手敲 `rollout restart`。

## 怎么部署

deplowly 作为一个 Pod 运行在目标 Kubernetes 集群内部，它用 ServiceAccount 身份跟 API Server 通信，而不是在本地机器上运行。

除了正常的 `Deployment` 资源描述（`deploy/deployment.yaml`），它还需要一组对应的 **RBAC 权限**（`deploy/rbac.yaml`）。具体来说，它的 ServiceAccount 至少要具备以下权限：

| 资源          | 操作                     | 用途                                                                                     |
| ------------- | ------------------------ | ---------------------------------------------------------------------------------------- |
| `deployments` | `get` / `list` / `patch` | 读取被监控 Deployment 的容器镜像，并在发现新 digest 时打 `restartedAt` 注解触发滚动更新  |
| `secrets`     | `get` / `list`           | 读取业务 Deployment 引用的 `imagePullSecrets`，用来做 registry 认证（获取 Bearer token） |

快速部署：

```bash
# 1. 本地先校验配置是否正确
uv run python -m deplowly config.yaml

# 2. 在集群里创建 RBAC（含 ServiceAccount / Role / RoleBinding）
kubectl apply -f deploy/rbac.yaml

# 3. 把 deplowly 本身部署进集群
kubectl apply -f deploy/deployment.yaml
```

> ⚠️ 安全提示：为了简化使用，deplowly 会读取业务 Deployment 的 `imagePullSecrets`，所以上述 SA 实际上能拿到业务镜像仓库的凭据，这在安全上属于「反模式」。因此不要在生产环境中使用。具体说明见 `deploy/rbac.yaml` 内的注释。

## 特性

- 仅比对远端 digest，不依赖镜像 tag 是否变化
- 遍历 Deployment 所有容器（含 initContainers），任一变化即重启
- `repo@sha256:...` 写死的镜像自动跳过（无法监控变更）
- 复用目标 Deployment 的 `imagePullSecrets` 进行 registry 认证
- 异常保守：registry 调用失败时不重启，下一轮继续
- 优雅退出：响应 SIGTERM/SIGINT

## 配置

deplowly 的配置是一个 YAML 文件，通过命令行参数传入。建议在容器启动时一定要显式指定配置路径。

容器内约定路径为 `/etc/deplowly/config.yaml`。

### 配置文件的内容

```yaml
default_interval: 60   # 秒，未单独设置 interval 的 target 使用
targets:
  - namespace: prod
    deployment: web-api
  - namespace: prod
    deployment: worker
    interval: 300       # 可覆盖全局间隔
```

### Kubernetes 用 ConfigMap 挂载

生产/集群场景推荐用 ConfigMap 把配置注入，而不是打包进镜像。仓库里 `deploy/deployment.yaml` 已经把这两件事都做了。
