#!/bin/bash

# # Full deployment (build + apply)
# ./deploy.sh all
# 
# # Only build and push image
# ./deploy.sh build
# 
# # Only deploy to Kubernetes
# ./deploy.sh apply
# 
# # Build with specific version
# ./deploy.sh build --version v1.0.0
# 
# # Deploy specific version
# ./deploy.sh apply --version v1.0.0
# 
# # Full deployment with specific version
# ./deploy.sh all --version v1.0.0
set -e

DOCKER_REGISTRY="docker.io"
DOCKER_USERNAME="iklob1"  
PROJECT_NAME="telegram-bot"
DOCKER_IMAGE="${DOCKER_REGISTRY}/${DOCKER_USERNAME}/${PROJECT_NAME}"
KUBERNETES_NAMESPACE="telegram-bot"
MANIFEST_FILE="kubernetes-manifest.yaml"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

print_message() {
    echo -e "${GREEN}==>${NC} ${1}"
}

print_error() {
    echo -e "${RED}ERROR:${NC} ${1}"
}

load_env() {
    if [ -f .env ]; then
        print_message "Loading .env file..."
        set -a
        source .env
        set +a
    else
        print_error ".env file not found"
        exit 1
    fi
}

check_requirements() {
    command -v docker >/dev/null 2>&1 || { print_error "Docker required"; exit 1; }
    command -v kubectl >/dev/null 2>&1 || { print_error "kubectl required"; exit 1; }
    command -v envsubst >/dev/null 2>&1 || { print_error "envsubst required"; exit 1; }
}

check_env_vars() {
    local required_vars=("TELEGRAM_TOKEN" "ANTHROPIC_API_KEY" "MONGO_PASSWORD" "MONGO_USER" "SENTRY_DSN")
    for var in "${required_vars[@]}"; do
        if [ -z "${!var}" ]; then
            print_error "$var not set"
            exit 1
        fi
    done
}

build_image() {
    local version=$1
    print_message "Building version ${version}"
    
    if ! docker info >/dev/null 2>&1; then
        print_error "Docker daemon not running"
        exit 1
    fi
    
    if ! docker login ${DOCKER_REGISTRY}; then
        print_error "Failed to login to Docker registry"
        exit 1
    fi
    
    docker build -t ${DOCKER_IMAGE}:${version} .
    if [ $? -ne 0 ]; then
        print_error "Docker build failed"
        exit 1
    fi
    
    docker push ${DOCKER_IMAGE}:${version}
    if [ $? -ne 0 ]; then
        print_error "Docker push failed"
        exit 1
    fi
    
    docker tag ${DOCKER_IMAGE}:${version} ${DOCKER_IMAGE}:latest
    docker push ${DOCKER_IMAGE}:latest
}

deploy_kubernetes() {
    local version=$1
    print_message "Deploying version ${version}"
    
    local temp_manifest=$(mktemp)
    sed "s|DOCKER_IMAGE_NAME|${DOCKER_IMAGE}:${version}|g" ${MANIFEST_FILE} > ${temp_manifest}
    
    envsubst < ${temp_manifest} | kubectl apply -f -
    
    rm -f ${temp_manifest}
}

wait_deployment() {
    local timeout=300  
    local start_time=$(date +%s)
    
    while true; do
        local current_time=$(date +%s)
        local elapsed=$((current_time - start_time))
        
        if [ ${elapsed} -gt ${timeout} ]; then
            print_error "Deployment timeout after ${timeout} seconds"
            exit 1
        fi
        
        # if kubectl -n ${KUBERNETES_NAMESPACE} rollout status deployment/${PROJECT_NAME} --timeout=10s; then
        if kubectl -n ${KUBERNETES_NAMESPACE} apply -f kubernetes-manifest.yaml; then
            break
        fi
        
        sleep 5
    done
}

show_help() {
    echo "Usage: $0 [OPTIONS]"
    echo "Options:"
    echo "  build               Only build and push Docker image"
    echo "  apply               Only deploy to Kubernetes"
    echo "  all                 Build and deploy (default)"
    echo "  --version VERSION   Specify version tag (default: timestamp)"
    echo "  --username NAME     Docker Hub username"
    exit 0
}

main() {
    local action="all"
    local version=$(date +%Y%m%d-%H%M%S)

    while [[ $# -gt 0 ]]; do
        case $1 in
            build|apply|all)
                action="$1"
                shift
                ;;
            --version)
                version="$2"
                shift 2
                ;;
            --username)
                DOCKER_USERNAME="$2"
                DOCKER_IMAGE="${DOCKER_REGISTRY}/${DOCKER_USERNAME}/${PROJECT_NAME}"
                shift 2
                ;;
            --help|-h)
                show_help
                ;;
            *)
                print_error "Unknown option $1"
                show_help
                ;;
        esac
    done

    if [ -z "${DOCKER_USERNAME}" ] || [ "${DOCKER_USERNAME}" = "your-dockerhub-username" ]; then
        print_error "Please specify your Docker Hub username with --username"
        exit 1
    fi

    load_env
    check_requirements
    check_env_vars

    case $action in
        build)
            build_image $version
            ;;
        apply)
            deploy_kubernetes $version
            wait_deployment
            ;;
        all)
            build_image $version
            deploy_kubernetes $version
            wait_deployment
            ;;
    esac

    print_message "Completed: $action (version: $version)"
}

main "$@"
