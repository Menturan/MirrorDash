#!/bin/bash
# In the container, as root: a mirror's /storage, the venv with the checkout's app, a site as an older
# host left it (the unit's user's, 2770, 660), a restore by pi, the real unit, then start it.
set -e
export UV_PYTHON_INSTALL_DIR=/opt/uvpython
S=/storage/mirrordash
M=$S/modules
mkdir -p $S /tmp/src
cp -a /src/mirrordash_core /src/pyproject.toml /src/README.md /tmp/src/
cp /src/LICENSE* /tmp/src/ 2>/dev/null || true
uv venv -q --python 3.14 $S/venv_a
uv pip install -q --python $S/venv_a/bin/python /tmp/src
ln -sfn venv_a $S/venv
chown -R pi:pi $S
P=$M/pkgs/md-probe_1  # md_probe as installed: in a folder of its own, its user's, linked from site_a
mkdir -p $P/md_probe $P/md_probe-1.0.dist-info $M/site_a/old_pkg $M/data/a $M/data/b $M/cache
cp /test/plugin.py $P/md_probe/plugin.py
touch $P/md_probe/__init__.py $M/site_a/old_pkg/x.py  # old_pkg: what a shared site of before left
printf 'Metadata-Version: 2.1\nName: md-probe\nVersion: 1.0\n' > $P/md_probe-1.0.dist-info/METADATA
printf '[mirrordash.modules]\nmd_probe = md_probe.plugin:Module\n' > $P/md_probe-1.0.dist-info/entry_points.txt
ln -sfn ../pkgs/md-probe_1 $M/site_a/md-probe
ln -sfn site_a $M/site
chown -R mirrordash-mod:mirrordash-mod $M
find $M -type d -exec chmod 2770 {} + ; find $M -type f -exec chmod 660 {} +
echo "b's secret" > $M/data/b/secret; echo restored > $M/data/a/restored
chown pi:mirrordash-mod $M/data/a/restored $M/data/b/secret $M/data/a; chmod 660 $M/data/a/restored $M/data/b/secret
chmod 2771 $M $M/data $M/cache  # mirrordash-hydrate.sh's part
chown -R 70002:70002 $P && chmod -R o+rX $P  # as its user installed it (forkserver.PACKAGE_UIDS)
chown -R 70000:70000 $M/site_a/old_pkg && chmod -R o+rX $M/site_a/old_pkg  # the shared site, as its user left it
echo '{"md-probe": 70002}' > $M/package_uids.json && chown mirrordash-mod:mirrordash-mod $M/package_uids.json
cp /src/scripts/image/rootfs/etc/systemd/system/mirrordash-modules.service /etc/systemd/system/
install -m 755 /src/scripts/image/rootfs/usr/local/bin/mirrordash-module-network /usr/local/bin/
echo 'pi ALL=(ALL) NOPASSWD: /usr/local/bin/mirrordash-module-network' > /etc/sudoers.d/mirrordash
printf '[Service]\nType=oneshot\nRemainAfterExit=yes\nExecStart=/bin/true\n' > /etc/systemd/system/mirrordash-storage-init.service
mkdir -p /etc/systemd/system/mirrordash-modules.service.d
dns=$(awk '/^nameserver/ {print $2; exit}' /etc/resolv.conf)  # no systemd-resolved stub in here
printf '[Service]\nMemoryMax=400M\nIPAddressAllow=%s\n' "$dns" > /etc/systemd/system/mirrordash-modules.service.d/test.conf
systemctl daemon-reload
systemctl start mirrordash-modules.service
sleep 3
systemctl is-active mirrordash-modules.service
