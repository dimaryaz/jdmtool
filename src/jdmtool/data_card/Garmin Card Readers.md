# Garmin Card Readers

The early version and current version differ in

* Uninitialized USB PID after plugging in:
  * `0x300` (early model)
  * `0x500` (current model)
* Firmware:
  * Early model: Stage 1 (and final) firmware is `Aviation Card Programmer Ver 3.02 Aug 10 2015 13:21:45` (at **45**)
  * Current model: Stage 1 firmware is `Aviation Card Programmer Ver 3.02 Aug 10 2015 13:21:51` (at **51**)
* Endpoints (they are identified automagically)
  * Read endpoint required for `read-database`: `0x82` (early model), `0x86` (current model)
  * Write endpoint required for `write-database`: `0x02` for both variants
* Adjusting `NO_CARD = 0x00090304` may or may not be required

## Hardware (early model, P/N 011-01277-00)

Top view:
![Image](images/105-00911-00%20Ver.%203%20Top.jpg)

Bottom view:
![Image](images/105-00911-00%20Ver.%203%20Bottom.jpg)
