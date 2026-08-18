# 🦉deplowly

[![GitHub](https://img.shields.io/badge/GitHub-biggates%2Fdeplowly-blue?logo=github)](https://github.com/biggates/deplowly)

## 名字的由来

deplowly = depl[owl]y

- depl_y — 来自 deploy，这是它的核心使命：操作 k8s 的 deployment
- owl — 猫头鹰，象征它像猫头鹰一样「不睡觉地盯着」镜像仓库

所以 deplowly 是一只「盯着部署的猫头鹰」🦉，专门替你盯着集群里的镜像有没有换新。

## 它是干什么的

简单说：当镜像仓库推了新的镜像，它就自动帮你把服务重启一下，让新版本生效。

很多人在开发模式下会这样配置 Kubernetes：集群里有若干 `Deployment`，它们的 `imagePullPolicy` 设成了 `always`。这意味着只要 Pod 被重新拉起，就会去仓库拉最新的镜像。

但问题来了 —— 你推了新镜像，Deployment 不会自己重启，正常情况下你得手动 `kubectl rollout restart deployment/xxx` 一下。

deplowly 就是把这个「手动重启」的过程自动化了的小工具。

它会周期性地检查你指定的 Deployment 对应的镜像在远端仓库里有没有变化（比对 `Docker-Content-Digest`，不靠 tag 名字）。一旦发现镜像内容变了，就自动对该 Deployment 执行 `rollout restart`，（通过打上 `kubectl.kubernetes.io/restartedAt` 注解），让 K8s 自己去拉去新的镜像。

> 一句话：**开发模式镜像自动更新兜底工具** —— 推完镜像不用再手敲 `rollout restart`。

## 它如何工作

作为开发场景下的工具，deplowly 的设计较为简单粗暴：

- 在 k8s 中开辟一个独立的 namespace deplowly, 其中包括一个 configmap 和一个 deployment
- configmap 中是 config.yaml 配置文件
- 这个 deployment 持续运行，隔一段时间就进行一次检查

在这个设计中，要让 deplowly 正确运行，需要较高的权限。deplowly 可以帮你生成设置权限的 yaml ，但不会帮你操作。

## 如何部署

deplowly 作为一个 Pod 运行在目标 Kubernetes 集群内部，它用 ServiceAccount 身份跟 API Server 通信，而不是在本地机器上运行。

整个部署过程只需要 kubectl，不需要在本机安装 uv / python 或任何编译环境：配置通过 ConfigMap 写进集群，校验也由集群里的一次性 Pod 完成。

除了正常的 `Deployment` 资源描述（`deploy/deployment.yaml`），它还需要一组对应的 RBAC 权限（`deploy/rbac.yaml`）。具体来说，它的 ServiceAccount 至少要具备以下权限：

| 资源          | 操作                     | 用途                                                                                     |
| ------------- | ------------------------ | ---------------------------------------------------------------------------------------- |
| `deployments` | `get` / `list` / `patch` | 读取被监控 Deployment 的容器镜像，并在发现新 digest 时打 `restartedAt` 注解触发滚动更新  |
| `secrets`     | `get` / `list`           | 读取业务 Deployment 引用的 `imagePullSecrets`，用来做 registry 认证（获取 Bearer token） |

快速部署：

```bash
# 0. 若 deplowly namespace 还不存在，先创建
kubectl apply -f deploy/namespace.yaml

# 1. 把配置写进集群。本地用任意编辑器写好 config.yaml 后，整体覆盖 ConfigMap：
kubectl create configmap deplowly-config -n deplowly \
  --from-file=config.yaml=config.yaml --dry-run=client -o yaml | kubectl apply -f -
#    小改配置也可以直接编辑：
kubectl edit configmap deplowly-config -n deplowly

# 2. 校验配置是否正确（一次性 Pod，跑完即删，不会常驻）
#    校验本地 config.yaml：
cat config.yaml | kubectl run deplowly-check --rm -i --restart=Never -n deplowly \
  --image=ghcr.io/biggates/deplowly:latest --image-pull-policy=Always -- check -
#    校验集群里 ConfigMap 的实际内容：
kubectl get configmap deplowly-config -n deplowly -o jsonpath='{.data.config\.yaml}' | \
  kubectl run deplowly-check --rm -i --restart=Never -n deplowly \
  --image=ghcr.io/biggates/deplowly:latest --image-pull-policy=Always -- check -

# 3. 在集群里创建 RBAC（含 ServiceAccount / Role / RoleBinding）
kubectl apply -f deploy/rbac.yaml

# 4. 把 deplowly 本身部署进集群
kubectl apply -f deploy/deployment.yaml
```

> 注：deplowly 只在启动时读取一次配置。改完 ConfigMap 后要让它生效，需要 `kubectl rollout restart deploy/deplowly -n deplowly`。

> ⚠️ 安全提示：为了简化使用，deplowly 会读取业务 Deployment 的 `imagePullSecrets`，所以上述 SA 实际上能拿到业务镜像仓库的凭据，这在安全上属于「反模式」。因此不要在生产环境中使用。具体说明见 `deploy/rbac.yaml` 内的注释。

## 为多 namespace 生成 RBAC

deplowly 自身只部署在 *一个* namespace（默认 `deplowly`），但它监控的 Deployment 往往散落在多个 namespace。每个目标 namespace 都需要一份独立的 `Role` + `RoleBinding`，把 `deplowly` 这个 ServiceAccount 绑定进去并授权该 ns 的 `deployments`/`secrets`。

与其手写多份 YAML，可以直接用 deplowly 自己生成：

```bash
# 集群已部署 deplowly 时，直接复用它的容器生成（配置已挂载在容器内，无需本机装环境）：
kubectl exec deploy/deplowly -n deplowly -- deplowly rbac --from-config /etc/deplowly/config.yaml | kubectl apply -f -

# 本机装了 uv / python 时的等价做法（集群里还没有 deplowly 时用）：
# 从配置文件读取所有要监控的 namespace（自动排除 deplowly 自身所在 ns）
uv run python -m deplowly rbac --from-config config.yaml | kubectl apply -f -

# 或者直接指定目标 namespace
uv run python -m deplowly rbac --namespace prod --namespace staging -o rbac-targets.yaml

# 自定义 ServiceAccount 名 / 所在 namespace（若你没用默认的 deplowly）
uv run python -m deplowly rbac --namespace prod --sa-name deplowly --sa-namespace deplowly
```

生成结果包含每个目标 namespace 的 `Role`（deployments 的 get/list/patch + secrets 的 get/list）和 `RoleBinding`（把指定 SA 绑定到该 Role），可直接 `kubectl apply`。

> 注：ServiceAccount 本身只需按 `deploy/rbac.yaml` 在 deplowly 所在 namespace 创建一次；本项目生成的清单不含 ServiceAccount，避免重复 apply 报错。

## 特性

- 仅比对远端 digest，不依赖镜像 tag 是否变化
- 遍历 Deployment 所有容器（含 initContainers），任一变化即重启
- `repo@sha256:...` 写死的镜像自动跳过（无法监控变更）
- 复用目标 Deployment 的 `imagePullSecrets` 进行 registry 认证
- 异常保守：registry 调用失败时不重启，下一轮继续
- 优雅退出：响应 SIGTERM/SIGINT

## 配置

deplowly 的配置是一个 YAML 文件，通过命令行参数传入。建议在容器启动时一定要显式指定配置路径。

容器内约定路径为 `/etc/deplowly/config.yaml`。想校验配置内容是否正确，可以用 `deplowly check`（一次性 Pod，见「怎么部署」）。

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
