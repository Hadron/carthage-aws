# carthage_aws
carthage_aws is a plugin for [Carthage](https://github.com/Hadron/carthage) to build/manage objects on Amazon Web Services.

## Development

Development environments can be setup how ever you want. 

To use a virtualenv you can follow these steps:

```
$ make build-venv # create the VIRTUALENV
$ source .venv/bin/activate
```

Once your venv is activated you can use the make targets.

If you prefer to install the project requirements system wide you can do that. 

```
$ pip install -e .[dev]
```

Depending on your OS you might receive an error when trying to `pip install` a package. In this case
you will likely have to use the `--break-system-packages` flag which will allow you to use the system 
python's `pip` command to install packages into python system wide. 

```
$ pip install -e .[dev] --break-system-packages
```

You can also install packages using OS distributions. Major OS's will package up python dependencies
and you can install them using your OS Package Manager. For example in ubuntu:

```
$ sudo apt install python3-boto3 pylint black python3-pytest
```

Finally you can use the podman container. For more information look at the [`Makefile`](Makefile).

```
$ make build-container
$ podman exec carthage_aws make quality
```

## Make Targets

* quality: Check the code against pylint.  It should be clean before commits
* check: Run tests against the code.



## Githooks

To install the githooks just run: 

```
make setup-githooks
```

This will install the githooks from the [`githooks`](/githooks) directory into your `.git/hooks` 
directory. 

### Pre-Commit hooks

The pre-commit hook will do a pylint on your code checking that any code you modified will pass 
the pylinter. This will run the make target `pylint`. 

The pylint target is designed to look for pylint installed locally first. (Either in your system 
python or virtualenv). If not found it'll default to building the container and running pylint
in the container. This is slow, especially, the first time. And it depends on Podman. So you'll 
likely want to install pylint locally.

## Tests

To run the tests you'll need an AWS account. 

Take a look at the Makefile to see how to use an API key.  Typically
for individual developers running tests, set up a profile using `aws
configure sso` and then set your profile name in the
`CARTHAGE_TEST_AWS_PROFILE` environment variable.

Before you run the tests ensure you've logged in using `aws sso login --profile <profile-name>`

* `make check` in activated venv or with system dependencies installed
* `podman exec carthage_aws make check` for containers.
