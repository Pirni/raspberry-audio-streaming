# !/bin/bash
# Install dependencies and set up the environment
sudo apt-get update
sudo apt-get install -y \
  python3 python3-venv python3-pip \
  hostapd dnsmasq \
  wireguard wireguard-tools \
  ffmpeg \
  avahi-daemon

# Install Python dependencies
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

cd user-config
cp config.example.json config.json
cd ..

cd systemd
sudo sh resolve-dirs.sh
sudo sh link-sym-files.sh
cd ..

sudo systemctl daemon-reload
sudo systemctl enable --now radio-station-web.service
sudo systemctl enable --now radio-station-controller.service

# Make hotspot mode deterministic
sudo systemctl unmask hostapd
sudo systemctl disable --now hostapd dnsmasq || true
