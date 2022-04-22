plugins:
- .
aws:
  region: ${config.aws.region}
%if config.aws.vpc_name:
  vpc_name: ${config.aws.vpc_name}
%endif
%if config.aws.vpc_id:
  vpc_id: ${config.aws.vpc_id}
%endif
authorized_keys: /dev/null
production_ssh_agent: true
