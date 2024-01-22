.PHONY: build run-pylint run-pytest
GIT_ROOT := $(shell git rev-parse --show-toplevel)
CONTAINER_NAME := "carthage_aws"

build:
	@if ! podman container exists $(CONTAINER_NAME); then \
		echo "Container carthage_aws does not exist. Building..."; \
		podman pull ghcr.io/hadron/carthage:latest ; \
		podman run -d --name $(CONTAINER_NAME) --privileged -v $(HOME)/.aws:/root/.aws -v $(GIT_ROOT):/carthage_aws --device=/dev/fuse carthage:latest ; \
		podman exec $(CONTAINER_NAME) apt update ; \
		podman exec $(CONTAINER_NAME) apt -y install python3-pytest python3-boto3 pylint ; \
	fi

run-pylint: build
	@if [ -z "$(FILES)" ]; then \
		podman exec -ti -w /carthage_aws $(CONTAINER_NAME) pylint $(shell git ls-files '*.py'); \
	else \
		podman exec -ti -w /carthage_aws $(CONTAINER_NAME) pylint $(FILES); \
	fi

run-pytest: build
	podman exec -ti -w/carthage_aws $(CONTAINER_NAME) && \
		pytest-3 -v && \
		--carthage-config=.github/${USER}_test_config.yml

clean:
	podman rm -f $(CONTAINER_NAME)
