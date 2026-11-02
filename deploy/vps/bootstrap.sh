#!/usr/bin/env bash
# First-boot user data (cloud-init) for an Ubuntu 24.04 x64 server that runs
# the Ghost templates in deploy/ghost/. No credentials belong in this file.
# It expects the provider to have installed your SSH key for root, copies that
# key to a new sudo account, turns off root and password logins, installs
# Docker, and creates /opt/wtf-adsb for the Compose project. Replace the
# account name below before use; rerunning the script is safe.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
admin_user=operator

if ! id "$admin_user" >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash --groups sudo "$admin_user"
fi
install -d -m 0700 -o "$admin_user" -g "$admin_user" "/home/$admin_user/.ssh"
test -s /root/.ssh/authorized_keys
install -m 0600 -o "$admin_user" -g "$admin_user" \
    /root/.ssh/authorized_keys "/home/$admin_user/.ssh/authorized_keys"
# Validate before installing: one invalid sudoers.d file disables sudo.
sudoers=$(mktemp)
printf '%s\n' "$admin_user ALL=(ALL) NOPASSWD:ALL" >"$sudoers"
visudo -cf "$sudoers"
install -m 0440 "$sudoers" "/etc/sudoers.d/$admin_user"
rm -f -- "$sudoers"

apt-get -o DPkg::Lock::Timeout=300 update
apt-get -o DPkg::Lock::Timeout=300 install -y ca-certificates curl unattended-upgrades
install -d -m 0755 /etc/apt/keyrings
curl --fail --silent --show-error --location \
    https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod 0644 /etc/apt/keyrings/docker.asc
printf '%s\n' \
    'deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable' \
    >/etc/apt/sources.list.d/docker.list
apt-get -o DPkg::Lock::Timeout=300 update
apt-get -o DPkg::Lock::Timeout=300 install -y \
    docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
install -d -m 0755 /etc/docker
daemon_config='{"log-driver":"local","log-opts":{"max-size":"10m","max-file":"3"}}'
systemctl enable --now docker
# A rerun must not restart every container when the configuration is unchanged.
if [[ "$(cat /etc/docker/daemon.json 2>/dev/null || true)" != "$daemon_config" ]]; then
    printf '%s\n' "$daemon_config" >/etc/docker/daemon.json
    systemctl restart docker
fi

if [[ ! -e /swapfile ]]; then
    fallocate -l 2G /swapfile
    chmod 0600 /swapfile
    mkswap /swapfile
fi
if ! swapon --show=NAME --noheadings | grep -Fxq /swapfile; then
    swapon /swapfile
fi
if ! grep -Eq '^/swapfile[[:space:]]' /etc/fstab; then
    printf '%s\n' '/swapfile none swap sw 0 0' >>/etc/fstab
fi
printf '%s\n' 'vm.swappiness=10' >/etc/sysctl.d/90-wtf-adsb.conf
sysctl -p /etc/sysctl.d/90-wtf-adsb.conf
install -d -m 0755 /etc/systemd/journald.conf.d
printf '%s\n' '[Journal]' 'SystemMaxUse=200M' >/etc/systemd/journald.conf.d/wtf-adsb.conf
systemctl restart systemd-journald
cat >/etc/apt/apt.conf.d/20auto-upgrades <<'APT'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT

install -d -m 0750 -o "$admin_user" -g "$admin_user" /opt/wtf-adsb
install -d -m 0700 -o "$admin_user" -g "$admin_user" /opt/wtf-adsb/secrets
cat >/etc/ssh/sshd_config.d/00-wtf-adsb.conf <<'SSH'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
SSH
sshd -t
systemctl reload-or-restart ssh
touch /opt/wtf-adsb/bootstrap-complete
