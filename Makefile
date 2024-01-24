.PHONY: build-container  clean-container setup-githooks quality build-venv clean-venv test check

PYTHON := python3
GIT_ROOT := $(shell git rev-parse --show-toplevel)
CONTAINER_NAME := "carthage_aws"

FILES = $(shell git ls-files '*.py')
# Defaurt pytest options blank
PYTEST_OPTIONS :=

all: check quality

pylint quality:
	pylint $(FILES)

check tes:
	$(PYTHON) -mpytest --carthage-config=.github/test_config.yml tests $(PYTEST_OPTIONS)

setup-githooks:
	@for hook in githooks/*; do \
		ln -sf ../../$$hook .git/hooks/`basename $$hook`; \
		echo "Installed $$hook"; \
	done

build-container:
	@set -e ;\
	if ! podman container exists $(CONTAINER_NAME); then \
		echo "Container carthage_aws does not exist. Building..."; \
		podman run --pull=newer -di --name $(CONTAINER_NAME) --privileged -v $(HOME)/.aws:/root/.aws \
			"-v$(GIT_ROOT):$(GIT_ROOT)" --device=/dev/fuse \
			"-eCARTHAGE_TEST_AWS_PROFILE" "-eACCESS_KEY_ID" \
			"-eAWS_SECRET_KEY" "-w$(CURDIR)" ghcr.io/hadron/carthage:latest /bin/sh; \
		podman exec $(CONTAINER_NAME) apt update ; \
		podman exec $(CONTAINER_NAME) apt -y install python3-pytest python3-boto3 pylint ; \
	else \
		echo Starting container; \
		podman start $(CONTAINER_NAME); \
	fi

clean-container:
	-podman rm -f $(CONTAINER_NAME)

build-venv:
	$(PYTHON) -mvenv .venv --system-site-packages --clear
	.venv/bin/pip install -e .[dev]

clean-venv:
	-rm -rf .venv
