#!/bin/bash
# Usage: run.sh <name> [vus] [duration]
# Runs the k6 script as a Job inside the cluster, so the numbers do not include a
# port-forward, and prints k6's summary.
set -e
name=$1; vus=${2:-50}; dur=${3:-30s}
here=$(cd "$(dirname "$0")" && pwd -W)
kubectl -n default create configmap k6-script --from-file=script.js="$here/script.js" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl -n default delete job "k6-$name" --ignore-not-found >/dev/null
kubectl -n default apply -f - >/dev/null <<YAML
apiVersion: batch/v1
kind: Job
metadata:
  name: k6-$name
spec:
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: k6
          image: grafana/k6:latest
          args: ["run", "/script/script.js"]
          env:
            - {name: BASE, value: "http://reverse-proxy.platform.svc:3000"}
            - {name: VUS, value: "$vus"}
            - {name: DURATION, value: "$dur"}
          volumeMounts: [{name: script, mountPath: /script}]
      volumes:
        - name: script
          configMap: {name: k6-script}
YAML
kubectl -n default wait --for=condition=complete job/k6-$name --timeout=300s >/dev/null
kubectl -n default logs job/k6-$name | grep -E "http_reqs|http_req_duration|checks_|http_req_failed|iterations" 
