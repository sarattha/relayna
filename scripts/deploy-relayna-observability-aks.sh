#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-observability}"
LOKI_IMAGE="${LOKI_IMAGE:-grafana/loki:3.5.5}"
ALLOY_IMAGE="${ALLOY_IMAGE:-grafana/alloy:v1.11.0}"
PROMETHEUS_IMAGE="${PROMETHEUS_IMAGE:-prom/prometheus:v3.7.3}"
TEMPO_IMAGE="${TEMPO_IMAGE:-grafana/tempo:latest}"
KUBE_STATE_METRICS_IMAGE="${KUBE_STATE_METRICS_IMAGE:-registry.k8s.io/kube-state-metrics/kube-state-metrics:v2.17.0}"
LOKI_STORAGE_SIZE="${LOKI_STORAGE_SIZE:-10Gi}"
PROMETHEUS_STORAGE_SIZE="${PROMETHEUS_STORAGE_SIZE:-20Gi}"
TEMPO_STORAGE_SIZE="${TEMPO_STORAGE_SIZE:-10Gi}"
LOKI_RETENTION="${LOKI_RETENTION:-168h}"
PROMETHEUS_RETENTION="${PROMETHEUS_RETENTION:-15d}"
TEMPO_RETENTION="${TEMPO_RETENTION:-168h}"
STORAGE_CLASS="${STORAGE_CLASS:-}"

STORAGE_CLASS_FIELD=""
if [[ -n "$STORAGE_CLASS" ]]; then
  STORAGE_CLASS_FIELD="        storageClassName: ${STORAGE_CLASS}"
fi

echo "Deploying Relayna observability stack to namespace '$NAMESPACE'."
echo "Set NAMESPACE, STORAGE_CLASS, image variables, storage sizes, or retention variables to override defaults."

kubectl apply -f - <<YAML
apiVersion: v1
kind: Namespace
metadata:
  name: ${NAMESPACE}
  labels:
    app.kubernetes.io/part-of: relayna-observability
YAML

kubectl apply -f - <<YAML
apiVersion: v1
kind: ConfigMap
metadata:
  name: loki-config
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/name: loki
    app.kubernetes.io/part-of: relayna-observability
data:
  loki.yaml: |
    auth_enabled: false

    server:
      http_listen_port: 3100

    common:
      path_prefix: /var/loki
      replication_factor: 1
      ring:
        kvstore:
          store: inmemory

    schema_config:
      configs:
        - from: 2024-01-01
          store: tsdb
          object_store: filesystem
          schema: v13
          index:
            prefix: index_
            period: 24h

    storage_config:
      filesystem:
        directory: /var/loki/chunks

    limits_config:
      retention_period: ${LOKI_RETENTION}
      allow_structured_metadata: true

    compactor:
      working_directory: /var/loki/compactor
      retention_enabled: true
      delete_request_store: filesystem
YAML

kubectl apply -f - <<YAML
apiVersion: v1
kind: Service
metadata:
  name: loki
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/name: loki
    app.kubernetes.io/part-of: relayna-observability
spec:
  selector:
    app.kubernetes.io/name: loki
  ports:
    - name: http
      port: 3100
      targetPort: http
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: loki
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/name: loki
    app.kubernetes.io/part-of: relayna-observability
spec:
  serviceName: loki
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: loki
  template:
    metadata:
      labels:
        app.kubernetes.io/name: loki
        app.kubernetes.io/part-of: relayna-observability
    spec:
      containers:
        - name: loki
          image: ${LOKI_IMAGE}
          args:
            - -config.file=/etc/loki/loki.yaml
          ports:
            - name: http
              containerPort: 3100
          readinessProbe:
            httpGet:
              path: /ready
              port: http
            initialDelaySeconds: 15
            periodSeconds: 10
          volumeMounts:
            - name: config
              mountPath: /etc/loki
            - name: data
              mountPath: /var/loki
      volumes:
        - name: config
          configMap:
            name: loki-config
  volumeClaimTemplates:
    - metadata:
        name: data
      spec:
        accessModes: ["ReadWriteOnce"]
${STORAGE_CLASS_FIELD}
        resources:
          requests:
            storage: ${LOKI_STORAGE_SIZE}
YAML

kubectl apply -f - <<YAML
apiVersion: v1
kind: ConfigMap
metadata:
  name: tempo-config
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/name: tempo
    app.kubernetes.io/part-of: relayna-observability
data:
  tempo.yaml: |
    server:
      http_listen_port: 3200
      grpc_listen_port: 9095

    distributor:
      receivers:
        otlp:
          protocols:
            grpc:
              endpoint: 0.0.0.0:4317
            http:
              endpoint: 0.0.0.0:4318

    ingester:
      trace_idle_period: 10s
      max_block_bytes: 1000000
      max_block_duration: 5m

    compactor:
      compaction:
        block_retention: ${TEMPO_RETENTION}

    storage:
      trace:
        backend: local
        wal:
          path: /var/tempo/wal
        local:
          path: /var/tempo/traces
---
apiVersion: v1
kind: Service
metadata:
  name: tempo
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/name: tempo
    app.kubernetes.io/part-of: relayna-observability
spec:
  selector:
    app.kubernetes.io/name: tempo
  ports:
    - name: http
      port: 3200
      targetPort: http
    - name: grpc
      port: 9095
      targetPort: grpc
    - name: otlp-grpc
      port: 4317
      targetPort: otlp-grpc
    - name: otlp-http
      port: 4318
      targetPort: otlp-http
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: tempo
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/name: tempo
    app.kubernetes.io/part-of: relayna-observability
spec:
  serviceName: tempo
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: tempo
  template:
    metadata:
      labels:
        app.kubernetes.io/name: tempo
        app.kubernetes.io/part-of: relayna-observability
    spec:
      containers:
        - name: tempo
          image: ${TEMPO_IMAGE}
          args:
            - -config.file=/etc/tempo/tempo.yaml
          ports:
            - name: http
              containerPort: 3200
            - name: grpc
              containerPort: 9095
            - name: otlp-grpc
              containerPort: 4317
            - name: otlp-http
              containerPort: 4318
          readinessProbe:
            httpGet:
              path: /ready
              port: http
            initialDelaySeconds: 15
            periodSeconds: 10
          volumeMounts:
            - name: config
              mountPath: /etc/tempo
            - name: data
              mountPath: /var/tempo
      volumes:
        - name: config
          configMap:
            name: tempo-config
  volumeClaimTemplates:
    - metadata:
        name: data
      spec:
        accessModes: ["ReadWriteOnce"]
${STORAGE_CLASS_FIELD}
        resources:
          requests:
            storage: ${TEMPO_STORAGE_SIZE}
YAML

kubectl apply -f - <<YAML
apiVersion: v1
kind: ServiceAccount
metadata:
  name: alloy
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/name: alloy
    app.kubernetes.io/part-of: relayna-observability
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: relayna-observability-alloy
  labels:
    app.kubernetes.io/name: alloy
    app.kubernetes.io/part-of: relayna-observability
rules:
  - apiGroups: [""]
    resources: ["pods", "namespaces", "nodes"]
    verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: relayna-observability-alloy
  labels:
    app.kubernetes.io/name: alloy
    app.kubernetes.io/part-of: relayna-observability
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: relayna-observability-alloy
subjects:
  - kind: ServiceAccount
    name: alloy
    namespace: ${NAMESPACE}
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: alloy-config
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/name: alloy
    app.kubernetes.io/part-of: relayna-observability
data:
  config.alloy: |
    logging {
      level  = "info"
      format = "logfmt"
    }

    discovery.kubernetes "pods" {
      role = "pod"
    }

    discovery.relabel "pod_logs" {
      targets = discovery.kubernetes.pods.targets

      rule {
        source_labels = ["__meta_kubernetes_namespace"]
        target_label  = "namespace"
      }

      rule {
        source_labels = ["__meta_kubernetes_pod_name"]
        target_label  = "pod"
      }

      rule {
        source_labels = ["__meta_kubernetes_pod_label_service"]
        target_label  = "service"
      }

      rule {
        source_labels = ["__meta_kubernetes_pod_label_app"]
        target_label  = "app"
      }

      rule {
        source_labels = ["__meta_kubernetes_pod_container_name"]
        target_label  = "container"
      }

      rule {
        source_labels = ["__meta_kubernetes_pod_uid", "__meta_kubernetes_pod_container_name"]
        separator     = "/"
        target_label  = "__path__"
        replacement   = "/var/log/pods/*\$1/*.log"
      }
    }

    loki.source.kubernetes "pods" {
      targets    = discovery.relabel.pod_logs.output
      forward_to = [loki.process.relayna.receiver]
    }

    loki.process "relayna" {
      stage.cri {}

      stage.json {
        expressions = {
          level          = "level",
          task_id        = "task_id",
          correlation_id = "correlation_id",
        }
      }

      stage.labels {
        values = {
          level = "level",
        }
      }

      forward_to = [loki.write.default.receiver]
    }

    loki.write "default" {
      endpoint {
        url = "http://loki.${NAMESPACE}.svc.cluster.local:3100/loki/api/v1/push"
      }
    }

    otelcol.receiver.otlp "relayna" {
      grpc {
        endpoint = "0.0.0.0:4317"
      }

      http {
        endpoint = "0.0.0.0:4318"
      }

      output {
        traces = [otelcol.processor.batch.relayna.input]
      }
    }

    otelcol.processor.batch "relayna" {
      output {
        traces = [otelcol.exporter.otlp.tempo.input]
      }
    }

    otelcol.exporter.otlp "tempo" {
      client {
        endpoint = "tempo.${NAMESPACE}.svc.cluster.local:4317"
        tls {
          insecure = true
        }
      }
    }
---
apiVersion: v1
kind: Service
metadata:
  name: alloy
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/name: alloy
    app.kubernetes.io/part-of: relayna-observability
spec:
  selector:
    app.kubernetes.io/name: alloy
  ports:
    - name: http
      port: 12345
      targetPort: http
    - name: otlp-grpc
      port: 4317
      targetPort: otlp-grpc
    - name: otlp-http
      port: 4318
      targetPort: otlp-http
---
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: alloy
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/name: alloy
    app.kubernetes.io/part-of: relayna-observability
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: alloy
  template:
    metadata:
      labels:
        app.kubernetes.io/name: alloy
        app.kubernetes.io/part-of: relayna-observability
    spec:
      serviceAccountName: alloy
      containers:
        - name: alloy
          image: ${ALLOY_IMAGE}
          args:
            - run
            - /etc/alloy/config.alloy
            - --server.http.listen-addr=0.0.0.0:12345
          ports:
            - name: http
              containerPort: 12345
            - name: otlp-grpc
              containerPort: 4317
            - name: otlp-http
              containerPort: 4318
          volumeMounts:
            - name: config
              mountPath: /etc/alloy
            - name: varlog
              mountPath: /var/log
              readOnly: true
      volumes:
        - name: config
          configMap:
            name: alloy-config
        - name: varlog
          hostPath:
            path: /var/log
YAML

kubectl apply -f - <<YAML
apiVersion: v1
kind: ServiceAccount
metadata:
  name: kube-state-metrics
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/name: kube-state-metrics
    app.kubernetes.io/part-of: relayna-observability
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: relayna-observability-kube-state-metrics
  labels:
    app.kubernetes.io/name: kube-state-metrics
    app.kubernetes.io/part-of: relayna-observability
rules:
  - apiGroups: [""]
    resources: ["configmaps", "secrets", "nodes", "pods", "services", "serviceaccounts", "resourcequotas", "replicationcontrollers", "limitranges", "persistentvolumeclaims", "persistentvolumes", "namespaces", "endpoints"]
    verbs: ["list", "watch"]
  - apiGroups: ["apps"]
    resources: ["statefulsets", "daemonsets", "deployments", "replicasets"]
    verbs: ["list", "watch"]
  - apiGroups: ["batch"]
    resources: ["cronjobs", "jobs"]
    verbs: ["list", "watch"]
  - apiGroups: ["autoscaling"]
    resources: ["horizontalpodautoscalers"]
    verbs: ["list", "watch"]
  - apiGroups: ["policy"]
    resources: ["poddisruptionbudgets"]
    verbs: ["list", "watch"]
  - apiGroups: ["storage.k8s.io"]
    resources: ["storageclasses", "volumeattachments"]
    verbs: ["list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: relayna-observability-kube-state-metrics
  labels:
    app.kubernetes.io/name: kube-state-metrics
    app.kubernetes.io/part-of: relayna-observability
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: relayna-observability-kube-state-metrics
subjects:
  - kind: ServiceAccount
    name: kube-state-metrics
    namespace: ${NAMESPACE}
---
apiVersion: v1
kind: Service
metadata:
  name: kube-state-metrics
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/name: kube-state-metrics
    app.kubernetes.io/part-of: relayna-observability
spec:
  selector:
    app.kubernetes.io/name: kube-state-metrics
  ports:
    - name: http
      port: 8080
      targetPort: http
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: kube-state-metrics
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/name: kube-state-metrics
    app.kubernetes.io/part-of: relayna-observability
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: kube-state-metrics
  template:
    metadata:
      labels:
        app.kubernetes.io/name: kube-state-metrics
        app.kubernetes.io/part-of: relayna-observability
    spec:
      serviceAccountName: kube-state-metrics
      containers:
        - name: kube-state-metrics
          image: ${KUBE_STATE_METRICS_IMAGE}
          ports:
            - name: http
              containerPort: 8080
YAML

kubectl apply -f - <<YAML
apiVersion: v1
kind: ServiceAccount
metadata:
  name: prometheus
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/name: prometheus
    app.kubernetes.io/part-of: relayna-observability
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: relayna-observability-prometheus
  labels:
    app.kubernetes.io/name: prometheus
    app.kubernetes.io/part-of: relayna-observability
rules:
  - apiGroups: [""]
    resources: ["nodes", "nodes/proxy", "services", "endpoints", "pods"]
    verbs: ["get", "list", "watch"]
  - nonResourceURLs: ["/metrics"]
    verbs: ["get"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: relayna-observability-prometheus
  labels:
    app.kubernetes.io/name: prometheus
    app.kubernetes.io/part-of: relayna-observability
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: relayna-observability-prometheus
subjects:
  - kind: ServiceAccount
    name: prometheus
    namespace: ${NAMESPACE}
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: prometheus-config
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/name: prometheus
    app.kubernetes.io/part-of: relayna-observability
data:
  prometheus.yml: |
    global:
      scrape_interval: 15s
      evaluation_interval: 15s

    scrape_configs:
      - job_name: prometheus
        static_configs:
          - targets:
              - localhost:9090

      - job_name: kube-state-metrics
        static_configs:
          - targets:
              - kube-state-metrics.${NAMESPACE}.svc.cluster.local:8080

      - job_name: kubernetes-cadvisor
        kubernetes_sd_configs:
          - role: node
        scheme: https
        tls_config:
          insecure_skip_verify: true
        bearer_token_file: /var/run/secrets/kubernetes.io/serviceaccount/token
        relabel_configs:
          - target_label: __address__
            replacement: kubernetes.default.svc:443
          - source_labels: [__meta_kubernetes_node_name]
            target_label: __metrics_path__
            replacement: /api/v1/nodes/\${1}/proxy/metrics/cadvisor

      - job_name: relayna-pods
        kubernetes_sd_configs:
          - role: pod
        relabel_configs:
          - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_scrape]
            action: keep
            regex: "true"
          - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_path]
            target_label: __metrics_path__
            regex: (.+)
          - source_labels: [__address__, __meta_kubernetes_pod_annotation_prometheus_io_port]
            target_label: __address__
            regex: ([^:]+)(?::\\d+)?;(\\d+)
            replacement: \${1}:\${2}
          - source_labels: [__meta_kubernetes_namespace]
            target_label: namespace
          - source_labels: [__meta_kubernetes_pod_name]
            target_label: pod
          - source_labels: [__meta_kubernetes_pod_container_name]
            target_label: container
          - source_labels: [__meta_kubernetes_pod_label_service]
            target_label: service
          - source_labels: [__meta_kubernetes_pod_label_app]
            target_label: app
---
apiVersion: v1
kind: Service
metadata:
  name: prometheus
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/name: prometheus
    app.kubernetes.io/part-of: relayna-observability
spec:
  selector:
    app.kubernetes.io/name: prometheus
  ports:
    - name: http
      port: 9090
      targetPort: http
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: prometheus
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/name: prometheus
    app.kubernetes.io/part-of: relayna-observability
spec:
  serviceName: prometheus
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: prometheus
  template:
    metadata:
      labels:
        app.kubernetes.io/name: prometheus
        app.kubernetes.io/part-of: relayna-observability
    spec:
      serviceAccountName: prometheus
      containers:
        - name: prometheus
          image: ${PROMETHEUS_IMAGE}
          args:
            - --config.file=/etc/prometheus/prometheus.yml
            - --storage.tsdb.path=/prometheus
            - --storage.tsdb.retention.time=${PROMETHEUS_RETENTION}
            - --web.enable-lifecycle
          ports:
            - name: http
              containerPort: 9090
          readinessProbe:
            httpGet:
              path: /-/ready
              port: http
            initialDelaySeconds: 15
            periodSeconds: 10
          volumeMounts:
            - name: config
              mountPath: /etc/prometheus
            - name: data
              mountPath: /prometheus
      volumes:
        - name: config
          configMap:
            name: prometheus-config
  volumeClaimTemplates:
    - metadata:
        name: data
      spec:
        accessModes: ["ReadWriteOnce"]
${STORAGE_CLASS_FIELD}
        resources:
          requests:
            storage: ${PROMETHEUS_STORAGE_SIZE}
YAML

echo "Waiting for observability pods to become ready..."
kubectl -n "$NAMESPACE" rollout status statefulset/loki --timeout=180s
kubectl -n "$NAMESPACE" rollout status daemonset/alloy --timeout=180s
kubectl -n "$NAMESPACE" rollout status deployment/kube-state-metrics --timeout=180s
kubectl -n "$NAMESPACE" rollout status statefulset/prometheus --timeout=180s
kubectl -n "$NAMESPACE" rollout status statefulset/tempo --timeout=180s

cat <<EOF

Relayna observability stack deployed.

Cluster DNS endpoints:
  Loki:       http://loki.${NAMESPACE}.svc.cluster.local:3100
  Prometheus: http://prometheus.${NAMESPACE}.svc.cluster.local:9090
  Tempo:      http://tempo.${NAMESPACE}.svc.cluster.local:3200
  Alloy OTLP: http://alloy.${NAMESPACE}.svc.cluster.local:4317

Annotate Relayna API, worker, and Studio backend pods that expose /metrics:
  prometheus.io/scrape: "true"
  prometheus.io/port: "8001"
  prometheus.io/path: "/metrics"

Use stable labels:
  service: <studio service_id>
  app: <api-or-worker-name>

Configure application OpenTelemetry exporters to send traces to:
  OTEL_EXPORTER_OTLP_ENDPOINT=http://alloy.${NAMESPACE}.svc.cluster.local:4317

Studio backend should allow AKS DNS egress:
  RELAYNA_STUDIO_CAPABILITY_REFRESH_ALLOWED_HOSTS=.svc.cluster.local

EOF
