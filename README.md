# carthage_aws
carthage_aws is a plugin for [Carthage](https://github.com/Hadron/carthage) to build/manage objects on Amazon Web Services.

## Development

Development environments can be setup how ever you want. 

To use a virtualenv you can follow these steps:

```
$ python -m venv .carthage_aws        # create the virtualenv
$ source .carthage_aws/bin/activate   # activate the virtualenv
$ pip install .[dev]                  # install the dependencies
```

If you prefer to install the project requirements system wide you can do that. 
Please refer to the [pyproject.toml](pyproject.toml) here you can see a list of the 
required dependencies.

Depending on your OS you might receive an error when trying to `pip install` a package. In this case
you will likely have to use the `--break-system-packages` flag which will allow you to use the system 
python's `pip` command to install packages into python system wide. 

```
$ pip install boto3 carthage pylint black --break-system-packages
```

You can also install packages using OS distributions. Major OS's will package up python dependencies
and you can install them using your OS Package Manager. For example in ubuntu:

```
$ sudo apt install python3-boto3 pylint black python3-pytest
```

Finally you can use the podman container. For more information look at the [`Makefile`](Makefile).


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
You'll also want to copy the `.github/test_config.yml` to something like `.github/$USER_test_config.ym`and then update the test_config to use your AWS SSO credentials. 

Before you run the tests ensure you've logged in using `aws sso login --profile <profile-name>`

Then you can run the tests either inside the container using the make command. 

Or you can run the tests in venv with `pytest -v --carthage-config=.github/$USER_test_config.yml`
