
# Installing udev rules

## Ubuntu (likely other debian variants), and Fedora

To install and reload udev rules on ubuntu:

    sudo cp 50-skybound.rules /etc/udev/rules.d/
    sudo udevadm control --reload-rules
    sudo udevadm trigger
