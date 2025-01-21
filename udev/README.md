
# Installing udev rules

Install rules for Skybound G2:

    sudo cp 50-skybound.rules /etc/udev/rules.d/

Install rules for Garmin Data Card Programmer:

    sudo cp 50-garmin.rules /etc/udev/rules.d/

Then reload udev:

    sudo udevadm control --reload-rules
    sudo udevadm trigger
