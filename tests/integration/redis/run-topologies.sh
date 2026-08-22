#!/usr/bin/env bash
set -euo pipefail

compose_file="tests/integration/redis/docker-compose.yml"
project_name="relayna-redis-topology-test"

compose() {
  docker compose \
    --project-name "$project_name" \
    --file "$compose_file" \
    --profile standalone \
    --profile replication \
    --profile cluster \
    --profile replicated-cluster \
    "$@"
}

cleanup() {
  compose ps --all
  compose down --remove-orphans
}

trap cleanup EXIT

run_tests() {
  RELAYNA_TEST_REDIS_URL="$1" \
  RELAYNA_TEST_REDIS_MODE="$2" \
  RELAYNA_TEST_REDIS_REPLICA_URLS="${3:-}" \
    .venv/bin/pytest \
      tests/test_redis_topologies_redis.py \
      tests/test_service_event_feed_redis.py \
      -q
}

cleanup
compose up --detach --wait standalone
run_tests "redis://localhost:16379/0" standalone
cleanup

compose up --detach --wait primary replica-1 replica-2
run_tests \
  "redis://localhost:16380/0" \
  standalone \
  "redis://localhost:16381/0,redis://localhost:16382/0"
cleanup

compose up --detach --wait cluster-1 cluster-2 cluster-3
compose exec --no-TTY cluster-1 redis-cli --cluster create \
  cluster-1:17001 cluster-2:17002 cluster-3:17003 \
  --cluster-replicas 0 --cluster-yes
run_tests "redis://localhost:17001/0" cluster
cleanup

compose up --detach --wait \
  replicated-cluster-1 replicated-cluster-2 replicated-cluster-3 \
  replicated-cluster-4 replicated-cluster-5 replicated-cluster-6
compose exec --no-TTY replicated-cluster-1 redis-cli --cluster create \
  replicated-cluster-1:17101 replicated-cluster-2:17102 replicated-cluster-3:17103 \
  replicated-cluster-4:17104 replicated-cluster-5:17105 replicated-cluster-6:17106 \
  --cluster-replicas 1 --cluster-yes
run_tests "redis://localhost:17101/0" cluster
