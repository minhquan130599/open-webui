# Terminal image for the broker: Open Terminal without sudo, run as a normal user, with what NCS AI tasks need
# preinstalled (Word / Excel / PDF / charts, Vietnamese fonts). The broker runs it read-only with all capabilities
# dropped, no swap, 1 CPU / 2 GB, on a network whose only way out is the logging egress proxy.
#
#   docker build -t ncs-terminal:latest terminal-broker/terminal-image
ARG BASE_IMAGE=ghcr.io/open-webui/open-terminal:latest
FROM ${BASE_IMAGE}

USER root

RUN set -eux; \
    export DEBIAN_FRONTEND=noninteractive; \
    apt-get update; \
    # Vietnamese-capable fonts (PDF from Word / charts without broken accents) and PDF tools
    apt-get install -y --no-install-recommends fonts-noto-core fonts-dejavu-core poppler-utils; \
    # LibreOffice headless for Word / Excel / PowerPoint -> PDF, only if the base image does not have it
    if ! command -v soffice >/dev/null 2>&1; then \
        apt-get install -y --no-install-recommends libreoffice-core-nogui libreoffice-writer-nogui \
            libreoffice-calc-nogui libreoffice-impress-nogui; \
    fi; \
    # Python libraries the tasks use, only the missing ones
    if ! python3 -c "import pandas, openpyxl, docx, pptx, matplotlib, xlsxwriter" >/dev/null 2>&1; then \
        python3 -m pip install --no-cache-dir --break-system-packages \
            pandas openpyxl python-docx python-pptx matplotlib xlsxwriter; \
    fi; \
    fc-cache -f; \
    # No privilege escalation inside the terminal
    apt-get purge -y sudo || true; \
    rm -f /etc/sudoers /etc/sudoers.d/* || true; \
    # Open Terminal's interpreter carries file capabilities (cap_setgid, for its multi-user mode). With every
    # capability dropped the kernel refuses to exec such a file ("bad interpreter: Operation not permitted").
    # One user per container needs none, and no file can use them here anyway: strip them from every binary
    # (copying a file drops its capabilities).
    for f in $(find / -xdev -type f -perm /111 -exec getcap {} + 2>/dev/null | awk '{print $1}'); do \
        cp -p "$f" "$f.nocap" && mv -f "$f.nocap" "$f"; \
    done; \
    test -z "$(find / -xdev -type f -perm /111 -exec getcap {} + 2>/dev/null)"; \
    test -n "$(command -v open-terminal)"; \
    apt-get autoremove -y; \
    rm -rf /var/lib/apt/lists/* /root/.cache; \
    # The broker mounts the per-user volume on /home/user and gives the shared terminal a tmpfs owned by uid 1000
    test "$(id -u user)" = "1000"; \
    chmod 700 /home/user

USER user
WORKDIR /home/user
