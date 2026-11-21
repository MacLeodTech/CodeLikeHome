# stdlib-only Python on Alpine, nothing installed. Dependabot bumps the digest.
FROM python:3.15-alpine@sha256:0f9552bb28711333da16915ec146f5e6bb44a7fdac9ced15dc17c2e0206346dd
RUN pip uninstall -y pip setuptools wheel
COPY forward.py /srv/forward.py
LABEL org.opencontainers.image.title="codelikehome-forwarder" \
      org.opencontainers.image.description="Relays a TCP port to a Unix socket so a bastion on a private network can reach an editor that has no TCP listener" \
      org.opencontainers.image.licenses="MIT"
# The compose file sets `user:` to the editor's uid so the relay may open the mode-600 socket; no user is baked in.
ENV PYTHONUNBUFFERED=1
EXPOSE 8443
CMD ["python", "/srv/forward.py"]
