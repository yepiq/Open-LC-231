# LC-231 BLE Read Protocol

This document describes the Bluetooth transport, frame format, and known
read-only data objects of the Grundfos LC-231. It is intended to contain the
information needed to implement an independent read client.

No write or pump-control operation is documented or supported.

## Evidence Labels

Every semantic interpretation uses one of these labels:

| Label | Meaning |
| --- | --- |
| **Confirmed** | The byte layout and field meaning are established for the LC-231. |
| **Hypothesis** | The interpretation is plausible but not proven on the LC-231. The reason for the hypothesis is stated next to it. |
| **Unknown** | The bytes or object exist, but no semantic meaning is assigned. |

Structural facts such as lengths, endianness, and checksums can be confirmed
without knowing every field's meaning. Unknown fields must be retained as raw
bytes and must not be silently discarded or guessed.

## Byte and Value Conventions

- Offsets are zero-based and relative to the object's data bytes, after the
  Class `0x0A` response envelope.
- Multi-byte integers and IEEE-754 `float32` values are big-endian.
- `u8`, `u16`, and `u32` mean unsigned integers of 1, 2, and 4 bytes.
- Object IDs are written as six hexadecimal digits. The first byte is the data
  ID and the final two bytes are the SubID.
- Hexadecimal values are shown without separators when they represent one
  field, for example `0201`.

## Protocol Terminology

GENIbus, short for Grundfos Electronics Network Intercommunications bus, is
Grundfos's pump-data communication protocol. The vendor messages documented
here use the same GENI framing conventions, transported inside BLE
characteristic writes and notifications rather than over a wired bus. This
document therefore calls each complete vendor message a GENI frame.

Within a GENI frame, the application protocol data unit (APDU) is the
application-level payload after the destination and source addresses.

## BLE Transport

### Discovery, Pairing, and Reconnection

**Confirmed**

The controller must be advertising before a new client can discover it. Its
advertisement identifies Grundfos service UUID `0xFE5D` as a service-data UUID;
it was not present in the advertisement's service-UUID list. A paired, bonded
connection is required before bidirectional vendor-protocol traffic succeeds.

A client should use this discovery procedure:

1. scan for advertisements carrying service data under `0xFE5D`;
2. if exactly one is present, retain the scanner's device object or
   platform-specific identifier;
3. if several are present, require an exact configured name or explicit user
   selection rather than choosing the first Grundfos device;
4. if none is present, ask the user to press the controller's Bluetooth button
   and scan again;
5. connect and request pairing/bonding, then retain the bond for later
   reconnects.

The advertised name can be installation-specific and must not be assumed to be
the model name.

The controller accepts only one BLE-central connection at a time. Grundfos GO
or any other BLE client must disconnect before another client connects.

The LC 231 installation and operating instructions distinguish two button
actions. Holding the connect button for 15 seconds enables or disables
Bluetooth. With Bluetooth enabled, a short press starts the connection process,
and the blue LED flashes until a device connects. The instructions do not state
a separate advertising timeout.

An existing bond can be restored without another button press while the
controller is advertising. If reconnecting through a saved address or
platform identifier fails, scan for `0xFE5D` and connect using the device
object returned by the current scan. This is a BLE discovery recovery step,
unrelated to the LC-231 GENI address or object protocol.

### Endpoint

**Confirmed**

- Protocol traffic uses characteristic
  `859cffd1-036e-432a-aa28-1a0085b87ba9`.
- The client writes requests to this characteristic and receives replies as
  notifications from the same characteristic.
- The characteristic exposes `read`, `notify`, and
  `write-without-response` properties on that characteristic. Protocol reads
  use writes followed by notifications; the GATT `read` property does not
  replace that exchange.
- GATT handles are not stable protocol identifiers. Resolve the characteristic
  by UUID after service discovery on every connection.

The confirmed session order is:

1. discover and connect to the selected controller;
2. establish or restore the BLE bond;
3. discover GATT services and resolve the vendor characteristic by UUID;
4. enable notifications and wait for subscription completion;
5. send serialized protocol requests and receive notification replies;
6. disable notifications and disconnect cleanly.

Requests must not be sent before notification subscription is active, because
replies are delivered on that notification path.

No additional vendor initialization exchange is required for the mapped
Class `0x0A` status reads. The minimal path is:

```text
paired connection -> resolve characteristic -> enable notifications
                  -> send one Class 0x0A read -> receive its reply
```

Generic Access and Device Information Service reads are optional and
independent. The grouped discovery queries described later are also not a
prerequisite for Class `0x0A` reads, and the Grundfos GO clock updates are not
a communication handshake.

### ATT Chunks and GENI Stream Reassembly

**Confirmed**

ATT writes and notifications are byte-stream fragments, not protocol-message
boundaries:

- one GENI frame can span several ATT chunks;
- one ATT notification can contain the end of one frame and the start or all
  of another frame;
- outbound GENI frames longer than the available ATT write payload can be sent
  as consecutive characteristic writes;
- `LEN` and CRC describe the complete GENI frame and must not be recalculated
  for individual ATT chunks.

Chunks belonging to one frame must remain contiguous and must not be
interleaved with another request.

A stream parser should retain incomplete bytes between callbacks, find a known
start byte, read `LEN`, wait for `LEN + 4` total bytes, emit that complete
frame, and continue parsing any remaining bytes. Length and CRC validation are
still mandatory after reassembly; finding a plausible start byte is not enough
to accept a frame.

### Frame Format

**Confirmed**

```text
offset  size  field
0       1     start byte
1       1     LEN
2       1     destination address
3       1     source address
4       LEN-2 APDU
2+LEN   2     CRC, big-endian
```

`LEN` counts the destination, source, and APDU bytes. It does not count the
start byte, the `LEN` byte itself, or the two CRC bytes. A complete frame is
therefore `LEN + 4` bytes.

| Start byte | Status | Meaning |
| --- | --- | --- |
| `0x27` | **Confirmed** | Client request |
| `0x24` | **Confirmed** | Controller reply |
| `0x26` | **Unknown** | `christoph2/GENIBus` identifies it as a GENI message frame; its LC-231 use is not mapped |

Confirmed read-request addressing:

| Direction | Destination | Source | Status |
| --- | --- | --- | --- |
| client to controller | `0xE7` | `0xF8` | **Confirmed**, preferred for direct reads |
| client to controller | `0xFF` | `0xF8` | **Confirmed**, broadcast destination also answers reads |
| controller to client | `0xF8` | `0xE7` | **Confirmed** |

### CRC

**Confirmed**

```text
algorithm family: CRC-16/CCITT
polynomial:       0x1021
initial value:    0xFFFF
final XOR:        0xFFFF
reflected:        no
input bytes:      LEN through the final APDU byte
wire order:       high byte, then low byte
```

For a complete frame, the CRC input is `frame[1:-2]`.

## Request/Reply Transaction Rules

**Confirmed**

The safest common transaction model is one outstanding logical request per BLE
connection:

1. discard or separately log complete, unassociated frames already waiting in
   the receive queue;
2. write one complete request, using consecutive ATT chunks when necessary;
3. wait for a complete reply frame;
4. require reply start `0x24`, destination `0xF8`, source `0xE7`, a valid CRC,
   and the expected APDU class and structural shape;
5. only after accepting the reply, issue the next request.

This serialization is required because ordinary Class `0x0A` replies do not
repeat the requested object ID, Class `0x07` replies do not repeat the string
ID, and grouped replies do not repeat selector IDs. Timing alone is not a
sufficient association rule. Unexpected but valid frames should remain
unassociated rather than being decoded as the current request's response.

The minimum required pacing between completed ordinary exchanges is
**Unknown**. The segmented identification sequence below is the exception:
its continuation requests are sent immediately after their preceding replies,
and no unrelated request may be inserted.

### Minimal Read-Client Algorithm

The following is sufficient for ordinary mapped object reads:

```text
device = scan for one selected FE5D advertiser
link = connect and establish or restore bond
characteristic = resolve 859cffd1-036e-432a-aa28-1a0085b87ba9
enable notifications on characteristic

for each object_id:
    apdu = 0a 03 + object_id
    request = frame(27, destination=e7, source=f8, apdu)
    write request, split into contiguous ATT chunks if required

    repeat until timeout:
        feed notification bytes into the LEN-based stream parser
        for each complete frame:
            validate length, CRC, 24/f8/e7 direction, and Class 0a
            accept only the expected response envelope or a known short response
            otherwise preserve the frame as unassociated

disable notifications and disconnect
```

For a normal response, validate the expected object type, version, and data
length before applying any object-specific decoder. On timeout or structural
mismatch, fail that read; do not reuse the last value as though it were a fresh
controller response.

## Class 0x0A Object Reads

### Request APDU

**Confirmed**

Read requests use this APDU:

```text
0a 03 <object_id:3>
```

| APDU field | Meaning |
| --- | --- |
| `0x0A` | Data-object class |
| `0x03` | Read operation |
| next 3 bytes | Object ID |

Complete request template:

```text
27 07 <destination> f8 0a 03 <object_id:3> <crc:2>
```

Known vectors:

```text
Pump 1 aggregate, destination e7: 27 07 e7 f8 0a 03 5d 00 01 45 4c
Pump 2 aggregate, destination e7: 27 07 e7 f8 0a 03 5d 00 02 75 2f
```

### Reply APDU and Object Envelope

**Confirmed**

A normal sequential read reply begins with Class `0x0A` followed by the Class
payload length:

```text
0a <payload_len> <payload...>
```

The byte after `0x0A` is a payload length, not a page number. For example,
`0a 23` introduces 35 payload bytes.

Most mapped objects use this seven-byte payload envelope:

```text
offset  size  field                       status
0       1     constant 00                 Confirmed value; meaning Unknown
1       2     object type                 Confirmed
3       1     object version              Confirmed
4       2     constant 0000               Confirmed value; meaning Unknown
6       1     object data length          Confirmed
7       n     object data                 Confirmed structural position
```

The data-length byte equals the number of bytes after the envelope. The Class
payload length equals `7 + data_length`.

The reply does not repeat the requested object ID. A client must therefore:

1. send only one object request at a time;
2. accept only a valid reply frame from source `0xE7` to destination `0xF8`;
3. validate frame length and CRC;
4. match the expected object type, version, and data length before decoding.

Some objects use a variable data length. Those objects are identified
explicitly below and must not be matched by timing alone.

### Non-success Responses

Two short response forms return no normal object envelope:

- APDU `0a 01 04`;
- APDU prefix `0a 81`; Object 77 reads returned `0a 81 4d`.

Their exact meanings are **Unknown** because no LC-231 error-code definition
has been found. Clients must preserve them as unclassified negative responses,
not decode them as object data. The `0a 81 4d` form is returned for Object 77
SubIDs 0-2; the apparent data-ID echo does not establish the error code's
meaning. The `0a 01 04` form is a repeatable non-success or no-data response,
but its numeric error name remains **Unknown**.

## Class 0x07 String Reads

Class `0x07` provides individual product-information strings. These reads are
structurally separate from Class `0x0A` objects.

### Request and Reply

**Confirmed**

The request APDU is:

```text
07 01 <string_id>
```

The reply APDU is:

```text
07 <encoded_length> <utf8_bytes> 00
```

`encoded_length` includes the terminating NUL byte. An empty string is encoded
as `07 01 00`. The frame CRC and stream-reassembly rules are the same as for
Class `0x0A` reads. The confirmed requests use broadcast destination `0xFF`;
use of service destination `0xE7` for Class `0x07` is **Unknown**.

Confirmed request vector for string ID `0x01`:

```text
27 05 ff f8 07 01 01 54 4f
```

Confirmed string IDs:

| String ID | Purpose | Status |
| ---: | --- | --- |
| `0x01` | Product type | **Confirmed** |
| `0x08` | Product number | **Confirmed** |
| `0x09` | Serial number | **Confirmed** |
| `0x0A` | Production code | **Confirmed** |
| `0x11` | Installation/system name | **Confirmed** |
| `0x14` | GSC version identifier | Identifier **Confirmed**; exact version scope **Unknown** |
| `0x32` | Application software version | **Confirmed** |
| `0x34` | Motor-control software version | **Confirmed** |
| `0x3A` | BLE software version | **Confirmed** |

IDs `0x0E`, `0x37`, `0x61`, and `0x65` can return valid empty strings. Their
purposes remain **Unknown**.

Grundfos uses GSC for a Standard Configuration data file. The `GSC_V...`
shape identifies string `0x14` with that configuration system, but it is
**Unknown** whether the version describes the file format, the applied
configuration schema, or another GSC layer.

The reply contains no string ID. Clients must therefore keep Class `0x07`
reads serialized and associate each structurally valid reply with the one
outstanding string request.

## Segmented Product-Identification Object

Object `59000f` is a Type `0x0105`, version 3 product-information bundle with a
declared data length of 123 bytes. It uses this three-request sequence:

```text
59000f  head, including the declared total length and first data chunk
590010  first continuation chunk
590011  second continuation chunk
```

Complete confirmed request vectors are:

```text
head, broadcast destination:       27 07 ff f8 0a 03 59 00 0f dc 94
continuation 1, service address:   27 07 e7 f8 0a 03 59 00 10 9b 9c
continuation 2, service address:   27 07 e7 f8 0a 03 59 00 11 8b bd
```

The corresponding reply APDUs are:

```text
0a 35 00 0105 03 0000 7b <46 data bytes>
0a 35 00 <52 data bytes>
0a 1a 00 <25 data bytes>
```

The first reply retains the ordinary object-envelope fields, but its `0x7b`
data-length value declares the complete 123-byte object rather than the 46
bytes present in that reply. Each continuation begins with a single `00`
marker instead of another object envelope. The chunks reassemble as
`46 + 52 + 25 = 123` bytes. The continuation requests must be made as one
sequence after the head.

The client-side state machine is:

```text
send 59000f -> accept head -> send 590010 -> accept chunk 1
             -> send 590011 -> accept chunk 2 -> validate 123-byte total
```

No ordinary inter-request pause or unrelated query is inserted inside this
sequence. The continuation replies do not carry their requested object IDs, so
they must be accepted only in this state. After a timeout, validation failure,
or disconnect, discard all partial data and restart from `59000f` on a new
sequence.

The dependency of `590010` and `590011` on the preceding `59000f` head is
confirmed. Whether `59000f` itself can be the first application request on a
new connection is **Unknown**. Ordinary Class `0x0A` status reads require no
application-level preamble.

**Confirmed structural layout after reassembly:**

```text
offset  size      meaning
0       10        unidentified binary header
10      variable  six big-endian u16-length-prefixed, NUL-terminated strings
end     1         unidentified trailing byte; value 00
```

The length of each string includes its terminating NUL. The first three strings
are product type, application software version, and product number. Later
string fields and the binary header must remain neutrally numbered until their
roles are established.

## Grouped Data-Item Reads

Packed data-item reads use Classes `0x02`, `0x04`, `0x05`, `0x0B`, `0x0E`,
and `0x0F`. These exchanges are structurally different from Class `0x0A`
objects.

**Confirmed request-group structure:**

```text
<class> <control_and_count> <selector_id:count>
```

The low six bits of `control_and_count` give the number of one-byte selector
IDs. The high two bits are control flags. Known flag values are `0x00` and
`0xC0`. A single APDU can concatenate groups from several classes.

**Confirmed reply-group structure:**

```text
<class> <data_byte_length> <packed_data:data_byte_length>
```

Reply groups occur in the same class order as their request groups. In the
mapped exchanges, each item in a group has a fixed width, so the raw value for
a selector can be recovered by dividing the returned byte length by the
requested selector count. Signedness, scaling, and units remain unknown unless
identified separately.

For example, the regular aggregate query contains these groups:

| Class | Selectors | Bytes per item | Identified meaning |
| ---: | --- | ---: | --- |
| `0x05` | `39` | 1 | **Unknown** |
| `0x0E` | `1e` | 4 | Controller local clock seconds, **Confirmed** |
| `0x02` | `94 95 96` | 1 | Unit family, unit type, unit version, **Confirmed** |
| `0x0B` | `41 5f` | 2 | **Unknown** |
| `0x0F` | `05 14 16 17` | 4 | **Unknown** |

Its complete 27-byte GENI request is:

```text
27 17 ff f8 05 01 39 0e 01 1e 02 03 94 95 96
               0b 02 41 5f 0f 04 05 14 16 17 18 bb
```

The matching reply APDU has the following structural template:

```text
05 01 <1 byte>
0e 04 <4 bytes>
02 03 <3 bytes>
0b 04 <4 bytes>
0f 10 <16 bytes>
```

To associate such a reply, require the same number and order of class groups as
the request. For each group, require that `data_byte_length` divides evenly by
the requested selector count before pairing fixed-width values with selectors.
If either condition fails, preserve the reply as raw data instead of guessing
its layout.

Class `0x02` selectors `0x94`-`0x96` carry unit-family, unit-type, and
unit-version metadata respectively.

Class `0x0E` selector `0x1E` encodes the controller's local wall-clock time as
seconds using the Unix epoch calendar. The scalar is already local time; a
client must use timezone-neutral calendar conversion rather than apply the
host's timezone offset. Behavior across timezone or daylight-saving changes is
**Unknown**.

Object `5e0065` provides the corresponding extended standard/calendar
representation; its layout is documented under Clock Objects.

These classes are not required for the mapped Class `0x0A` status reads. A
client must preserve still-unknown selectors numerically rather than expose
them under guessed names.

## Object Index

The type and version columns describe the confirmed response-envelope
signature.

| Object | Type | Version | Data bytes | Coverage | Purpose |
| --- | ---: | ---: | ---: | --- | --- |
| `560000` | `0x00E4` | 1 | 6 | **Confirmed** | Current Pump 1 and Pump 2 control selections |
| `580000` | `0x023A` | 1 | 2 in normal state | Empty state **Confirmed**; nonzero layout **Hypothesis** | Active alarms |
| `58000b` | `0x023A` | 1 | 2 in normal state | Empty state **Confirmed**; nonzero layout **Hypothesis** | Active warnings |
| `520c00` | `0x00F5` | 2 | 21 | Two measurements **Confirmed**; remainder **Unknown** | CIO1 analog level-input status |
| `520c01`-`520c07` | `0x00D3` | 1 | 1 | Zero/inactive state **Confirmed** | Other terminal logic states |
| `5a0003` | `0x0109` | 1 | 13 | Some fields **Confirmed**; remainder **Unknown** | Water level and pump state |
| `5d0000` | `0x00F7` | 1 | 12 | Some fields **Confirmed**; one field **Unknown** | Controller runtime counters |
| `5d0001` | `0x00F8` | 2 | 28 | Some fields **Confirmed**; one field **Unknown** | Pump 1 aggregate counters |
| `5d0002` | `0x00F8` | 2 | 28 | Some fields **Confirmed**; one field **Unknown** | Pump 2 aggregate counters |
| `5d0051` | `0x0160` | 1 | 12 | **Confirmed** | Pump 1 today/yesterday counters |
| `5d0052` | `0x0160` | 1 | 12 | **Confirmed** | Pump 2 today/yesterday counters |
| `5d0093` | `0x0314` | 3 | 13 | Some fields **Confirmed**; remainder **Unknown** | Mains-voltage status |
| `5d00d3` | `0x0311` | 3 | 12 | **Confirmed** | Pump 1 last-run electrical values |
| `5d00d4` | `0x0311` | 3 | 12 | **Confirmed** | Pump 2 last-run electrical values |
| `570001` | `0x00E8` | 1 | 38 | Two fields **Confirmed**; one field **Unknown** | Pump temperatures and one unidentified temperature |
| `570045` | `0x0100` | 2 | 41 | Some fields **Confirmed**; remainder **Unknown** | Pump 1 live electrical values |
| `570047` | `0x0268` | 1 | 8 | One field **Confirmed**; remainder **Unknown** | Pump 1 live power factor |
| `570085` | `0x0100` | 2 | 41 | Some fields **Confirmed**; remainder **Unknown** | Pump 2 live electrical values |
| `570087` | `0x0268` | 1 | 8 | One field **Confirmed**; remainder **Unknown** | Pump 2 live power factor |

## Controller Status Objects

### `560000`: Current Pump Control Selections

```text
offset  size  type   meaning                    status
0       2     bytes  unidentified header        Unknown
2       2     bytes  Pump 1 control selection   Confirmed
4       2     bytes  Pump 2 control selection   Confirmed
```

Confirmed selection values for either pump:

| Value | Meaning |
| --- | --- |
| `0000` | Auto |
| `0201` | Off |
| `0205` | On |

The two slots are independent. This object reports the controller's current
selection; it does not define or imply a BLE control operation.

### `5a0003`: Water Level and Pump State

```text
offset  size  type     meaning                         status
0       4     float32  water level, metres             Confirmed
4       6     bytes    unidentified                    Unknown
10      1     u8       water above pump stop level     Confirmed as boolean 0/1
11      1     bits     pump running flags              Partially Confirmed
12      1     u8       unidentified                    Unknown
```

Confirmed flags at offset 11:

| Mask | Meaning | Status |
| ---: | --- | --- |
| `0x20` | Pump 1 running | **Confirmed** |
| `0x40` | Pump 2 running | **Confirmed** |
| all other bits | unidentified status flags | **Unknown** |

The two running bits are independent and may be set simultaneously.

### `520c00`: CIO1 Analog Level Input

This record reports the CIO1 analog-input status:

```text
offset  size  type     meaning                         status
0       8     bytes    unidentified                    Unknown
8       4     float32  unidentified                    Unknown
12      4     float32  measured level, metres          Confirmed
16      4     float32  analog input current, mA        Confirmed
20      1     u8       unidentified                    Unknown
```

### `520c01`-`520c07`: Terminal Logic States

The remaining terminal-status objects are one-byte logic states:

| Object | Terminal |
| --- | --- |
| `520c01` | CIO2 |
| `520c02` | DI1 |
| `520c03` | DI2 |
| `520c04` | Relay 1 |
| `520c05` | Relay 2 |
| `520c06` | DIO1 |
| `520c07` | DIO2 |

The value `00` means inactive. The nonzero encoding is **Unknown**, so clients
must retain the raw byte in addition to any Boolean view.

### `5d0000`: Controller Runtime Counters

```text
offset  size  type  meaning                                      status
0       4     u32   unidentified shared controller counter       Unknown semantic; equality with pump records Confirmed
4       4     u32   controller total-on time, seconds             Confirmed
8       4     u32   time with at least one pump running, seconds  Confirmed
```

Offset 8 counts elapsed wall-clock time during which one or both pumps run. It
does not sum overlapping Pump 1 and Pump 2 runtimes.

### `5d0093`: Mains Voltage

```text
offset  size  type     meaning                         status
0       4     float32  phase-to-neutral voltage, V     Confirmed
4       4     float32  phase-to-phase voltage, V       Confirmed
8       4     float32  unidentified voltage-like value Unknown
12      1     u8       mains-connection code            Confirmed for code 3
```

Code `3` means three phase with neutral. Other codes remain **Unknown**.

### `580000` and `58000b`: Active Conditions

| Object | Confirmed role |
| --- | --- |
| `580000` | Active alarms in the normal, empty state |
| `58000b` | Active warnings in the normal, empty state |

Both return type `0x023A`, version 1. With no active condition, each returned
two data bytes `0000`.

**Hypothesis:** a nonzero response is a variable-length array of big-endian
`u16` condition codes. ALPHA HWR maps these same Object 88 SubIDs and type as
condition-code arrays, but the LC-231 nonzero encoding remains **Unknown**.
Until it is confirmed, condition codes must remain numeric.

## Pump Counter Objects

### `5d0001` and `5d0002`: Aggregate Pump Counters

`5d0001` is Pump 1 and `5d0002` is Pump 2. Both use the same layout:

```text
offset  size  type  meaning                               status
0       4     u32   lifetime starts                       Confirmed
4       2     u16   starts in latest hour                 Confirmed
6       2     u16   starts in latest 24 hours             Confirmed
8       4     u32   lifetime runtime, seconds              Confirmed
12      4     u32   runtime in latest 24 hours, seconds    Confirmed
16      4     u32   latest runtime, seconds                Confirmed
20      4     u32   unidentified shared controller counter Unknown semantic; matches 5d0000 offset 0
24      4     u32   controller total-on time, seconds      Confirmed
```

### `5d0051` and `5d0052`: Today and Yesterday

`5d0051` is Pump 1 and `5d0052` is Pump 2. Both use the same confirmed layout:

```text
offset  size  type  meaning
0       2     u16   starts today
2       2     u16   starts yesterday
4       4     u32   runtime today, seconds
8       4     u32   runtime yesterday, seconds
```

## Pump Electrical Objects

### `570045` and `570085`: Live Voltage, Current, and Power

`570045` is Pump 1 and `570085` is Pump 2. Both use the same 41-byte layout:

```text
offset  size  type     meaning                   status
0       4     float32  live AC voltage, V         Confirmed
4       4     float32  unidentified               Unknown
8       4     float32  live motor current, A      Confirmed
12      4     float32  live active power, W       Confirmed
16      20    bytes    unidentified float slots   Unknown
36      5     bytes    unidentified trailing data Unknown
```

Current and power become zero when the corresponding pump is stopped; voltage
can remain present.

### `570047` and `570087`: Live Power Factor

`570047` is Pump 1 and `570087` is Pump 2:

```text
offset  size  type     meaning            status
0       4     float32  live power factor  Confirmed
4       4     bytes    unidentified       Unknown
```

### `5d00d3` and `5d00d4`: Last-Run Electrical Values

`5d00d3` is Pump 1 and `5d00d4` is Pump 2. Both use the same confirmed layout:

```text
offset  size  type     meaning
0       4     float32  last-run motor current, A
4       4     float32  last-run power factor
8       4     float32  last-run active power, W
```

These values refresh for a pump operating cycle and remain available after the
pump stops. They are not maxima: the retained endpoint can be lower than
values observed earlier in the same run. The controller's precise sampling or
aggregation method is **Unknown**. They are not a substitute for the live
`5700xx` objects.

### `570001`: Temperature Measurements

```text
offset  size  type     meaning                            status
0       4     float32  unidentified temperature, degrees Unknown source
4       14    bytes    unidentified                       Unknown
18      4     float32  Pump 1 temperature, degrees       Confirmed
22      8     bytes    unidentified                       Unknown
30      4     float32  Pump 2 temperature, degrees       Confirmed
34      4     bytes    unidentified                       Unknown
```

The offset-30 value responds to Pump 2 operation. Its paired offset-18 channel
is Pump 1. The offset-0 measurement is distinct and may represent the
controller electronics or its environment.

## Configuration Objects

Only read responses are documented. A decoded setting does not establish its
write encoding or all consequences of changing it.

| Object | Type | Version | Bytes | Purpose | Coverage |
| --- | ---: | ---: | ---: | --- | --- |
| `5a0000` | `0x00ED` | 5 | 37 | Level-control settings | Some fields **Confirmed**; remainder **Unknown** |
| `560001` | `0x00E3` | 1 | 4 | Manual-start return settings | Confirmed |
| `560002` | `0x00E5` | 2 | 7 | Anti-seizing timing | Two fields **Confirmed**; one byte **Unknown** |
| `5b0001` | `0x0103` | 1 | 3 | Multi-pump settings | Some fields **Confirmed**; one field **Unknown** |
| `5b0002` | `0x017C` | 1 | 9 | Run-and-pause settings | Some fields **Confirmed**; one field **Unknown** |
| `570005` | `0x00E6` | 1 | 3 | Automatic-restart limit | Confirmed |
| `570004` | `0x00FB` | 1 | 11 | Automatic alarm reset | One field **Confirmed**; remainder **Unknown** |
| `570007` | `0x0113` | 1 | 25 | Mains connection | One field **Confirmed**; remainder **Unknown** |
| `570008` | `0x0259` | 1 | 5 | Starts-per-hour limits | Confirmed |
| `5700d2` | `0x015E` | 1 | 1 | Blocked-pump restart limit | Confirmed |
| `570074` / `5700b4` | `0x0107` | 1 | 15 | Pump 1 / Pump 2 motor protection | Some fields **Confirmed**; remainder **Unknown** |
| `570075` / `5700b5` | `0x0115` | 1 | 16 | Parallel motor-protection record | Some fields **Confirmed**; remainder **Unknown** |
| `5801cc` | `0x0361` | 3 | 19 | Under-voltage detection | Confirmed |
| `5801cd` | `0x0362` | 3 | 19 | Over-voltage detection | Confirmed |
| `560067` / `560068` | `0x01A0` | 2 | 22 | Limit 1 / Limit 2 configuration | Disabled state **Confirmed**; remainder **Unknown** |
| `520800` | `0x00EE` | 1 | 16 | CIO1 analog-input assignment | Some fields **Confirmed**; remainder **Unknown** |
| `520801`-`520803`, `520806`-`520807` | `0x00F0` | 1 | 4 | Digital-input assignments | **Confirmed** for known codes |
| `520804`-`520805` | `0x00F1` | 1 | 4 | Relay-output assignments | **Confirmed** for known codes |

### I/O Terminal Assignments

`520800` describes the configured CIO1 analog input:

```text
offset  size  type     meaning                    status
0       1     u8       enabled                    Confirmed as boolean
1       5     bytes    assignment/unit codes      Unknown
6       4     float32  configured minimum, metres Confirmed
10      4     float32  configured maximum, metres Confirmed
14      2     bytes    assignment/signal code     Unknown
```

The remaining assignment codes are **Unknown**.

The seven other assignment records use this compact layout:

```text
offset  size  type  meaning
0       1     u8    enabled
1       1     u8    assignment-kind code
2       2     u16   function code
```

Confirmed assignment-kind values are `0x1E` for a normally-open digital input
and `0x40` for a relay output. Confirmed function codes:

| Code | Function |
| ---: | --- |
| `0x0007` | All alarms and warnings |
| `0x000A` | Dry running |
| `0x003C` | High level |
| `0x003D` | Stop Pump 1 |
| `0x003E` | Start Pump 1 |
| `0x003F` | Start Pump 2 |
| `0x0180` | High-level alarm |

Other assignment-kind and function codes remain **Unknown**.

### `5a0000`: Level Control

```text
offset  size  type     meaning                          status
0       9     bytes    unidentified                     Unknown
9       4     float32  dry-running level, metres        Confirmed
13      4     float32  Pump 1 stop level, metres        Confirmed
17      4     float32  Pump 1 start level, metres       Confirmed
21      4     float32  Pump 2 start level, metres       Confirmed
25      4     float32  high level, metres               Confirmed
29      2     u16      high-level after-run, seconds    Confirmed
31      2     u16      stop delay, seconds              Confirmed
33      4     bytes    unidentified                     Unknown
```

Signal-detection time is a documented LC-231 level-control setting, but its
BLE object and field offset are **Unknown**.

### `560001`: Manual-Start Return

```text
offset  size  type  meaning                         status
0       1     u8    automatic return enabled        Confirmed as boolean
1       2     u16   forced-start duration, seconds  Confirmed
3       1     u8    return selection                Confirmed
```

Confirmed return-selection values:

| Value | Meaning |
| ---: | --- |
| 0 | Auto |
| 1 | Off |

The record describes configured behavior. Runtime behavior for other value
combinations is **Unknown**.

### `560002`: Anti-Seizing Timing

```text
offset  size  type  meaning                    status
0       1     u8    unidentified               Unknown
1       4     u32   anti-seizing interval, s   Confirmed
5       2     u16   anti-seizing run time, s   Confirmed
```

The leading byte has no confirmed enable/disable meaning.

### `5b0001`: Multi-Pump Configuration

```text
offset  size  type  meaning                         status
0       1     u8    unidentified                    Unknown
1       1     u8    inter-pump delay, seconds       Confirmed
2       1     u8    maximum simultaneously running  Confirmed
```

### `5b0002`: Run and Pause

```text
offset  size  type  meaning                    status
0       1     u8    enabled                    Confirmed as boolean
1       4     u32   maximum run time, seconds  Confirmed
5       2     u16   unidentified               Unknown
7       2     u16   pump pause time, seconds   Confirmed
```

### Restart and Starts-Per-Hour Limits

`570005`:

```text
offset  size  type  meaning
0       1     u8    automatic-restart limit enabled
1       1     u8    allowed restarts
2       1     u8    interval, hours
```

`570008`:

```text
offset  size  type  meaning
0       1     u8    starts-per-hour limits enabled
1       2     u16   warning limit
3       2     u16   alarm limit
```

`5700d2` contains one confirmed `u8`: allowed blocked-pump restarts.

### Automatic Alarm Reset and Mains Connection

`570004` byte 0 is the automatic-alarm-reset selection. Confirmed values are:

| Value | Selection |
| ---: | --- |
| 0 | No automatic reset |
| 1 | All except pump critical |
| 2 | All alarms |

Bytes 1-10 remain unknown.

`570007` byte 0 is the mains-connection code. Code `3` means three phase with
neutral. The same code appears at `5d0093` byte 12. The remaining 24 bytes
contain six finite `float32` values with **Unknown** meanings.

### Limit Monitoring

`560067` and `560068` are the Limit 1 and Limit 2 configuration records. An
all-zero 22-byte record represents the disabled state. The nonzero encoding
and remaining field layout are **Unknown**.

The adjacent `560069` and `56006a` status records both contain a `float32` at
offset 13 that tracks the measured water level. Their pairing with Limit 1 and
Limit 2 is a **Hypothesis** based on object adjacency and parallel structure.
All other fields remain **Unknown**.

### Motor Protection

`570074` is Pump 1 and `5700b4` is Pump 2:

```text
offset  size  type     meaning                    status
0       3     bytes    unidentified               Unknown
3       4     float32  nominal current, A         Confirmed
7       1     u8       IEC trip class             Confirmed
8       1     u8       overheat-protection code   Confirmed numeric value; code meanings incomplete
9       6     bytes    unidentified               Unknown
```

The parallel objects `570075` and `5700b5` contain the same three confirmed
fields at offsets 4, 8, and 9 respectively. Their role relative to the first
pair and all remaining bytes are unknown.

### Under- and Over-Voltage Detection

`5801cc` is under-voltage detection and `5801cd` is over-voltage detection.
Both use this confirmed layout:

```text
offset  size  type     meaning
0       1     u8       enabled
1       4     float32  phase-to-neutral warning threshold, V
5       4     float32  phase-to-neutral alarm threshold, V
9       2     u16      detection time, seconds
11      4     float32  phase-to-phase warning threshold, V
15      4     float32  phase-to-phase alarm threshold, V
```

## Clock Objects

Grundfos GO can configure the controller clock in automatic or manual mode.
Automatic clock configuration uses the `5e0064`, `5e0066`, and `5e0067` object
group. That functional role is **Confirmed**. Object `5e0064` also records a
manual clock-setting transaction. Its date/time fields and the daylight-saving
schedule are decoded below, while the UTC-offset record is only partially
usable.

The [official LC Modbus functional profile](https://www.grundfos-eica.com/fileadmin/public/Redaktion/Dokumente/PDF/Functional_profiles/99406791_0223_Modbus_LC_level_controller_Functional_Profile_GB_online.pdf)
exposes the same RTC in two forms: a Unix-time scalar and separate
human-readable second, minute, hour, day, month, and year registers. The same
semantic split is visible in the observed GENIbus data model: Class `0x0E`
selector `0x1E` is the scalar representation, and Object `5e0065` is the
extended standard/calendar representation. The Modbus evidence supports this
interpretation but does not define the GENIbus field order or widths.

### `5e0065`: Controller Local Time

Type `0x0142`, version 1, 12 data bytes:

```text
offset  size  type  meaning                         status
0       2     u16   year                            Confirmed
2       1     u8    month                           Confirmed
3       1     u8    day                             Confirmed
4       1     u8    hour                            Confirmed
5       1     u8    minute                          Confirmed
6       1     u8    second                          Confirmed
7       1     u8    hundredths of a second          Confirmed
8       1     u8    unidentified; observed as 0     Unknown
9       1     u8    ISO weekday, Monday = 1         Confirmed
10      1     u8    unidentified flag; observed 1   Unknown
11      1     u8    time-setting mode: 0 manual, 1 auto Confirmed
```

Byte 11 mirrors the automatic/manual selection in `5e0064`. The meaning of
byte 10 remains **Unknown**.

### `5e0064`: Clock Configuration

Type `0x0141`, version 1, 10 data bytes:

```text
offset  size  type  meaning                              status
0       1     u8    time-setting mode: 0 manual, 1 auto   Confirmed
1       2     u16   year                                 Confirmed
3       1     u8    month                                Confirmed
4       1     u8    day                                  Confirmed
5       1     u8    hour                                 Confirmed
6       1     u8    minute                               Confirmed
7       1     u8    second                               Confirmed
8       1     u8    hundredths of a second               Hypothesis
9       1     u8    unidentified; observed as 0          Unknown
```

The date/time fields are the timestamp included in the last clock-configuration
transaction, whether the selected mode is automatic or manual.

### `5e0066`: Daylight-Saving Schedule

Type `0x0143`, version 1, 10 data bytes:

```text
offset  size  type  meaning                                      status
0       1     u8    daylight-saving schedule enabled             Confirmed
1       1     u8    start month                                  Confirmed
2       1     u8    start ISO weekday; Monday = 1                Confirmed
3       1     u8    start week selector; 1-4, 5 = last           Confirmed
4       1     u8    start transition hour                        Confirmed
5       1     u8    end month                                    Confirmed
6       1     u8    end ISO weekday; Monday = 1                  Confirmed
7       1     u8    end week selector; 1-4, 5 = last             Confirmed
8       1     u8    end transition hour                          Confirmed
9       1     u8    daylight-saving adjustment, minutes          Confirmed
```

When byte 0 is zero, the remaining bytes may also be zero. When enabled, the
transition fields define the recurring start and end rules and byte 9 defines
the clock adjustment applied during that interval.

### `5e0067`: Standard UTC Offset

`5e0067` is Type `0x024D`, version 1, with two data bytes:

```text
offset  size  type  meaning                         status
0       1     u8    non-negative UTC-offset hours   Confirmed for non-negative offsets
1       1     u8    UTC-offset minutes              Confirmed for non-negative offsets
```

For example, `0200` represents UTC+02:00 and `051e` represents UTC+05:30.

The record is not reliable for negative UTC offsets. After an automatic clock
sync from a negative-offset timezone, the controller can store `0000` while
setting its local wall clock correctly. Distinct negative-offset timezones can
therefore have the same `5e0067` value. Consumers cannot determine from
`0000` alone whether it represents UTC+00:00 or an unrepresented negative
offset.

## Event Log

Object 88 provides a 40-slot alarm/warning history ring.

| Object range | Type | Version | Bytes | Meaning | Status |
| --- | ---: | ---: | ---: | --- | --- |
| `5827d7` | `0x00F3` | 1 | 7 | Ring metadata | Some fields **Confirmed**; one field **Unknown** |
| `5827d8`-`5827ff` | `0x00F4` | 2 | 13 | Forty ring slots | Confirmed |

### Metadata Record

```text
offset  size  type  meaning             status
0       2     u16   latest event ID      Confirmed
2       2     u16   available entries    Confirmed
4       2     u16   ring capacity        Confirmed
6       1     u8    unidentified         Unknown
```

### Event Record

```text
offset  size  type  meaning                    status
0       2     u16   event ID                   Confirmed
2       2     u16   Grundfos event code        Confirmed numeric value
4       1     u8    event source               Confirmed for known values
5       1     u8    related device number      Confirmed for known values
6       1     u8    event condition            Confirmed for known values
7       4     u32   controller-local time scalar Confirmed
11      2     u16   event-specific detail       Confirmed numeric value
```

The event time uses the same Unix-epoch calendar encoding as Class `0x0E`
selector `0x1E`: it is already expressed as the controller's local wall clock
and must be rendered without applying the client's timezone offset. Converting
it to an absolute timestamp using `5e0067` inherits that object's limitation
for negative UTC offsets.

Known source values:

| Value | Meaning |
| ---: | --- |
| 0 | System |
| 6 | Pump |

Known device values are 0 for no related pump, 1 for Pump 1, and 2 for Pump 2.

Known condition values:

| Value | Meaning |
| ---: | --- |
| 0 | None |
| 1 | Alarm appeared |
| 2 | Alarm disappeared |
| 3 | Warning appeared |
| 4 | Warning disappeared |

Confirmed code/detail meanings:

| Field | Value | Meaning |
| --- | ---: | --- |
| event code | 9 | Phase sequence reversal |
| event code | 25 | Setup conflict |
| detail for code 25 | 1005 | Level control settings wrong |

Ring slots wrap, so populated records must be ordered by event ID rather than
by SubID.

## Unknown and Candidate Objects

These object IDs are not part of the confirmed decoded surface.

| Object | Structure | Status |
| --- | --- | --- |
| `560003` | Type `0x0102`, version 1, two bytes | **Unknown.** |
| `580019` | Type `0x010A`, version 2, ten bytes; five `u16` fields | **Unknown.** |
| `5d0096` | Type `0x03B0`, version 1, 24 bytes; six float-shaped fields | **Unknown.** |
| `570043`, `570083` | Type `0x00FC`, version 1, five bytes; includes an unavailable-float candidate | **Unknown.** |
| `5d0041`, `5d0044` | Type `0x00F9` / `0x00FA`, version 1 | **Hypothesis:** service-related. Values can be interpreted as 1000 hours / 60000 minutes, matching the time-to-service vocabulary in the official LC Modbus functional profile; individual field roles are **Unknown**. |
| `5d0091` | Type `0x018F`, version 1, four bytes `7fffffff` | **Unknown/unavailable.** |
| `524e21`, `524e61` | Type `0x01A8`, version 1 records with 22 and 46 data bytes | **Unknown.** |
| `4d0000`, `4d0001`, `4d0002` | Short `0a814d` negative response; no object envelope | **Unsupported:** not an LC-231 energy source. |

The controller product model includes system and per-pump accumulated-energy
values, and the official integration profiles define corresponding read-only
energy counters. Their BLE source and encoding remain **Unknown**. The
unsupported Object 77 reads above must not be presented as those counters.

## Compatibility and Failure Boundaries

- These layouts are confirmed for one LC-231 installation. Compatibility with
  other controller variants and firmware versions is **Unknown**.
- A client must fail closed when type, version, length, CRC, or addressing does
  not match the expected record.
- Unknown enum values and flag bits must be surfaced numerically, not coerced
  into the nearest known meaning.

## References

Official Grundfos material provides product behavior and a semantic vocabulary
for comparison. It does not document the BLE object IDs or prove that Modbus
register layouts apply to this protocol:

- [LC 231 installation and operating instructions](https://api.grundfos.com/literature/Grundfosliterature-6142502.pdf)
- [Grundfos controller instructions defining GSC configuration data files](https://api.grundfos.com/literature/Grundfosliterature-6908979.pdf)
- [LC 231 EICA integration page](https://www.grundfos-eica.com/lc-231/modbus-tcp.html)
- [LC 2x1 / LC 2x2 Modbus functional profile](https://www.grundfos-eica.com/fileadmin/public/Redaktion/Dokumente/PDF/Functional_profiles/99406791_0223_Modbus_LC_level_controller_Functional_Profile_GB_online.pdf)
- [Grundfos CIM module Modbus register workbook](https://www.grundfos-eica.com/fileadmin/public/Redaktion/Dokumente/PDF/Functional_profiles/Grundfos_CIM_module_Modbus_profiles.xlsx)

Independent research on related GENI and Grundfos BLE transports helps explain
frame-layer structure. Product-specific object maps are not copied unless the
same behavior is independently confirmed on the LC-231:

- [`christoph2/GENIBus` protocol notes](https://github.com/christoph2/GENIBus/blob/master/docs/protocol.md)
- [ALPHA HWR GENI-over-BLE architecture](https://eman.github.io/alpha-hwr/protocol/ble_architecture/)
- [ALPHA HWR wire format](https://eman.github.io/alpha-hwr/protocol/wire_format/)
