# maneyko.imap_cloud_sync

The role that deploys this repo onto a host: a system user, a clone at
`/opt/imap-cloud-sync`, per-account secrets in `/etc/imap-cloud-sync/secrets/`,
AWS credentials for the service user, and the systemd timer that runs the sync.

`requirements.yml`:

```yaml
collections:
  - name: git@github.com:maneyko/imap-cloud-sync.git#/ansible
    type: git
    version: main
```

```yaml
- hosts: all
  roles:
    - role: maneyko.imap_cloud_sync.deploy
      vars:
        config:  "{{ app_config }}"
        secrets: "{{ app_secrets }}"
```

`config` and `secrets` are deliberately generic: the caller holds one structure
per app and hands it over whole. Both are declared in
`roles/deploy/meta/argument_specs.yaml` and validated before the role runs:

    ansible-doc -t role -M roles maneyko.imap_cloud_sync.deploy

`secrets.users` is a list of complete account TOML documents, exactly as the
uploader wants them on disk. Each is written to
`/etc/imap-cloud-sync/secrets/<username>.toml`, where `<username>` is read back
out of the TOML itself — so the list needs no keys and no parallel structure to
keep in sync.

The clone is pulled over SSH from a private repo, so the play needs agent
forwarding (`ansible_ssh_extra_args: "-A"`) and `SSH_AUTH_SOCK` kept across
`sudo`.
