import esphome.codegen as cg
from esphome.components import binary_sensor, ble_client, sensor, text_sensor
import esphome.config_validation as cv
from esphome.const import (
    CONF_ID,
    DEVICE_CLASS_CONNECTIVITY,
    DEVICE_CLASS_CURRENT,
    DEVICE_CLASS_DISTANCE,
    DEVICE_CLASS_DURATION,
    DEVICE_CLASS_POWER,
    DEVICE_CLASS_POWER_FACTOR,
    DEVICE_CLASS_PROBLEM,
    DEVICE_CLASS_RUNNING,
    DEVICE_CLASS_TEMPERATURE,
    DEVICE_CLASS_TIMESTAMP,
    DEVICE_CLASS_VOLTAGE,
    ENTITY_CATEGORY_DIAGNOSTIC,
    STATE_CLASS_MEASUREMENT,
    STATE_CLASS_TOTAL_INCREASING,
    UNIT_AMPERE,
    UNIT_CELSIUS,
    UNIT_EMPTY,
    UNIT_HOUR,
    UNIT_METER,
    UNIT_MILLIAMP,
    UNIT_MINUTE,
    UNIT_SECOND,
    UNIT_VOLT,
    UNIT_WATT,
)

AUTO_LOAD = ["binary_sensor", "sensor", "text_sensor"]
DEPENDENCIES = ["ble_client"]
CODEOWNERS = ["@ngorchilov"]

grundfos_lc231_ns = cg.esphome_ns.namespace("grundfos_lc231")
GrundfosLC231Component = grundfos_lc231_ns.class_(
    "GrundfosLC231Component", cg.Component, ble_client.BLEClientNode
)

CONF_FAST_UPDATE_INTERVAL = "fast_update_interval"
CONF_REGULAR_UPDATE_INTERVAL = "regular_update_interval"
CONF_SLOW_UPDATE_INTERVAL = "slow_update_interval"


def measurement_schema(unit, accuracy, device_class=None, **kwargs):
    return sensor.sensor_schema(
        unit_of_measurement=unit,
        accuracy_decimals=accuracy,
        device_class=device_class,
        state_class=STATE_CLASS_MEASUREMENT,
        **kwargs,
    )


def counter_schema(*, total=False):
    return sensor.sensor_schema(
        unit_of_measurement=UNIT_EMPTY,
        accuracy_decimals=0,
        state_class=(
            STATE_CLASS_TOTAL_INCREASING if total else STATE_CLASS_MEASUREMENT
        ),
    )


def duration_schema(unit, accuracy, *, total=False):
    return sensor.sensor_schema(
        unit_of_measurement=unit,
        accuracy_decimals=accuracy,
        device_class=DEVICE_CLASS_DURATION,
        state_class=(
            STATE_CLASS_TOTAL_INCREASING if total else STATE_CLASS_MEASUREMENT
        ),
    )


def diagnostic_setting_schema(unit, accuracy, device_class=None):
    kwargs = {}
    if device_class is not None:
        kwargs["device_class"] = device_class
    return sensor.sensor_schema(
        unit_of_measurement=unit,
        accuracy_decimals=accuracy,
        entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
        **kwargs,
    )


BASE_SENSORS = {
    "water_level": measurement_schema(UNIT_METER, 3, DEVICE_CLASS_DISTANCE),
    "cio1_level": measurement_schema(
        UNIT_METER,
        3,
        DEVICE_CLASS_DISTANCE,
        entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
    ),
    "cio1_input_current": measurement_schema(
        UNIT_MILLIAMP,
        3,
        DEVICE_CLASS_CURRENT,
        entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
    ),
    "controller_total_on_time": duration_schema(UNIT_HOUR, 2, total=True),
    "controller_active_pumping_time": duration_schema(UNIT_HOUR, 2, total=True),
    "mains_phase_to_neutral_voltage": measurement_schema(
        UNIT_VOLT, 1, DEVICE_CLASS_VOLTAGE
    ),
    "mains_phase_to_phase_voltage": measurement_schema(
        UNIT_VOLT, 1, DEVICE_CLASS_VOLTAGE
    ),
    "mains_unidentified_voltage": measurement_schema(
        UNIT_VOLT,
        1,
        DEVICE_CLASS_VOLTAGE,
        entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
    ),
    "unidentified_temperature": measurement_schema(
        UNIT_CELSIUS,
        2,
        DEVICE_CLASS_TEMPERATURE,
        entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
    ),
    "pump_1_temperature": measurement_schema(
        UNIT_CELSIUS, 2, DEVICE_CLASS_TEMPERATURE
    ),
    "pump_2_temperature": measurement_schema(
        UNIT_CELSIUS, 2, DEVICE_CLASS_TEMPERATURE
    ),
    "high_level_after_run_time": diagnostic_setting_schema(
        UNIT_SECOND, 0, DEVICE_CLASS_DURATION
    ),
    "stop_delay_time": diagnostic_setting_schema(
        UNIT_SECOND, 0, DEVICE_CLASS_DURATION
    ),
    "forced_start_time": diagnostic_setting_schema(
        UNIT_SECOND, 0, DEVICE_CLASS_DURATION
    ),
    "inter_pump_delay": diagnostic_setting_schema(
        UNIT_SECOND, 0, DEVICE_CLASS_DURATION
    ),
    "maximum_running_pumps": diagnostic_setting_schema(UNIT_EMPTY, 0),
    "maximum_run_time": diagnostic_setting_schema(
        UNIT_SECOND, 0, DEVICE_CLASS_DURATION
    ),
    "pump_pause_time": diagnostic_setting_schema(
        UNIT_SECOND, 0, DEVICE_CLASS_DURATION
    ),
    "rtc_unidentified_byte_10": diagnostic_setting_schema(UNIT_EMPTY, 0),
    "protocol_errors": sensor.sensor_schema(
        unit_of_measurement=UNIT_EMPTY,
        accuracy_decimals=0,
        state_class=STATE_CLASS_TOTAL_INCREASING,
        entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
    ),
}

PUMP_SENSOR_METRICS = {
    "lifetime_runs": counter_schema(total=True),
    "runs_last_hour": counter_schema(),
    "runs_last_24_hours": counter_schema(),
    "lifetime_runtime": duration_schema(UNIT_HOUR, 2, total=True),
    "runtime_last_24_hours": duration_schema(UNIT_MINUTE, 1),
    "last_run_duration": duration_schema(UNIT_MINUTE, 1),
    "runs_today": counter_schema(),
    "runs_yesterday": counter_schema(),
    "runtime_today": duration_schema(UNIT_MINUTE, 1),
    "runtime_yesterday": duration_schema(UNIT_MINUTE, 1),
    "live_voltage": measurement_schema(UNIT_VOLT, 1, DEVICE_CLASS_VOLTAGE),
    "live_current": measurement_schema(UNIT_AMPERE, 3, DEVICE_CLASS_CURRENT),
    "live_power": measurement_schema(UNIT_WATT, 1, DEVICE_CLASS_POWER),
    "live_power_factor": measurement_schema(
        UNIT_EMPTY, 3, DEVICE_CLASS_POWER_FACTOR
    ),
    "last_run_current": measurement_schema(
        UNIT_AMPERE,
        3,
        DEVICE_CLASS_CURRENT,
        entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
    ),
    "last_run_power_factor": measurement_schema(
        UNIT_EMPTY,
        3,
        DEVICE_CLASS_POWER_FACTOR,
        entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
    ),
    "last_run_power": measurement_schema(
        UNIT_WATT,
        1,
        DEVICE_CLASS_POWER,
        entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
    ),
}

BINARY_SENSORS = {
    "connected": binary_sensor.binary_sensor_schema(
        device_class=DEVICE_CLASS_CONNECTIVITY,
        entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
    ),
    "above_stop_level": binary_sensor.binary_sensor_schema(),
    "pump_1_running": binary_sensor.binary_sensor_schema(
        device_class=DEVICE_CLASS_RUNNING
    ),
    "pump_2_running": binary_sensor.binary_sensor_schema(
        device_class=DEVICE_CLASS_RUNNING
    ),
    "active_alarm": binary_sensor.binary_sensor_schema(
        device_class=DEVICE_CLASS_PROBLEM,
    ),
    "active_warning": binary_sensor.binary_sensor_schema(
        device_class=DEVICE_CLASS_PROBLEM,
    ),
    "automatic_time_setting": binary_sensor.binary_sensor_schema(
        entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
    ),
    "run_and_pause_enabled": binary_sensor.binary_sensor_schema(
        entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
    ),
}

TEXT_SENSORS = {
    "pump_1_control_selection": text_sensor.text_sensor_schema(),
    "pump_2_control_selection": text_sensor.text_sensor_schema(),
    "manual_start_return_mode": text_sensor.text_sensor_schema(
        entity_category=ENTITY_CATEGORY_DIAGNOSTIC
    ),
    "active_alarms_raw": text_sensor.text_sensor_schema(
        entity_category=ENTITY_CATEGORY_DIAGNOSTIC
    ),
    "active_warnings_raw": text_sensor.text_sensor_schema(
        entity_category=ENTITY_CATEGORY_DIAGNOSTIC
    ),
    "last_alarm": text_sensor.text_sensor_schema(
        entity_category=ENTITY_CATEGORY_DIAGNOSTIC
    ),
    "last_warning": text_sensor.text_sensor_schema(
        entity_category=ENTITY_CATEGORY_DIAGNOSTIC
    ),
    "last_alarm_time": text_sensor.text_sensor_schema(
        device_class=DEVICE_CLASS_TIMESTAMP,
        entity_category=ENTITY_CATEGORY_DIAGNOSTIC
    ),
    "last_warning_time": text_sensor.text_sensor_schema(
        device_class=DEVICE_CLASS_TIMESTAMP,
        entity_category=ENTITY_CATEGORY_DIAGNOSTIC
    ),
    "protocol_status": text_sensor.text_sensor_schema(
        entity_category=ENTITY_CATEGORY_DIAGNOSTIC
    ),
    "product_type": text_sensor.text_sensor_schema(
        entity_category=ENTITY_CATEGORY_DIAGNOSTIC
    ),
    "product_number": text_sensor.text_sensor_schema(
        entity_category=ENTITY_CATEGORY_DIAGNOSTIC
    ),
    "serial_number": text_sensor.text_sensor_schema(
        entity_category=ENTITY_CATEGORY_DIAGNOSTIC
    ),
    "production_code": text_sensor.text_sensor_schema(
        entity_category=ENTITY_CATEGORY_DIAGNOSTIC
    ),
    "application_software": text_sensor.text_sensor_schema(
        entity_category=ENTITY_CATEGORY_DIAGNOSTIC
    ),
    "motor_control_software": text_sensor.text_sensor_schema(
        entity_category=ENTITY_CATEGORY_DIAGNOSTIC
    ),
    "ble_software": text_sensor.text_sensor_schema(
        entity_category=ENTITY_CATEGORY_DIAGNOSTIC
    ),
    "system_name": text_sensor.text_sensor_schema(
        entity_category=ENTITY_CATEGORY_DIAGNOSTIC
    ),
    "gsc_version": text_sensor.text_sensor_schema(
        entity_category=ENTITY_CATEGORY_DIAGNOSTIC
    ),
    "controller_local_time": text_sensor.text_sensor_schema(
        device_class=DEVICE_CLASS_TIMESTAMP,
        entity_category=ENTITY_CATEGORY_DIAGNOSTIC
    ),
    "last_clock_synchronization": text_sensor.text_sensor_schema(
        device_class=DEVICE_CLASS_TIMESTAMP,
        entity_category=ENTITY_CATEGORY_DIAGNOSTIC
    ),
    "dst_schedule": text_sensor.text_sensor_schema(
        entity_category=ENTITY_CATEGORY_DIAGNOSTIC
    ),
    "standard_utc_offset": text_sensor.text_sensor_schema(
        entity_category=ENTITY_CATEGORY_DIAGNOSTIC
    ),
    "level_control_prefix_raw": text_sensor.text_sensor_schema(
        entity_category=ENTITY_CATEGORY_DIAGNOSTIC
    ),
    **{
        f"clock_record_{object_id}_raw": text_sensor.text_sensor_schema(
            entity_category=ENTITY_CATEGORY_DIAGNOSTIC
        )
        for object_id in (
            "5e0000",
            "5e0001",
            "5e0002",
            "5e0003",
            "5e0004",
        )
    },
}


schema = cv.Schema(
    {
        cv.GenerateID(): cv.declare_id(GrundfosLC231Component),
        cv.Optional(CONF_FAST_UPDATE_INTERVAL, default="2s"):
            cv.positive_time_period_milliseconds,
        cv.Optional(CONF_REGULAR_UPDATE_INTERVAL, default="30s"):
            cv.positive_time_period_milliseconds,
        cv.Optional(CONF_SLOW_UPDATE_INTERVAL, default="5min"):
            cv.positive_time_period_milliseconds,
    }
).extend(cv.COMPONENT_SCHEMA).extend(ble_client.BLE_CLIENT_SCHEMA)

for key, value in BASE_SENSORS.items():
    schema = schema.extend({cv.Optional(key): value})
for pump in (1, 2):
    for metric, value in PUMP_SENSOR_METRICS.items():
        schema = schema.extend({cv.Optional(f"pump_{pump}_{metric}"): value})
for key, value in BINARY_SENSORS.items():
    schema = schema.extend({cv.Optional(key): value})
for key, value in TEXT_SENSORS.items():
    schema = schema.extend({cv.Optional(key): value})

CONFIG_SCHEMA = schema


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await ble_client.register_ble_node(var, config)

    cg.add(
        var.set_update_intervals(
            config[CONF_FAST_UPDATE_INTERVAL].total_milliseconds,
            config[CONF_REGULAR_UPDATE_INTERVAL].total_milliseconds,
            config[CONF_SLOW_UPDATE_INTERVAL].total_milliseconds,
        )
    )

    for index, key in enumerate(BASE_SENSORS):
        if sensor_config := config.get(key):
            entity = await sensor.new_sensor(sensor_config)
            cg.add(var.set_sensor(index, entity))

    for pump_index, pump in enumerate((1, 2)):
        for metric_index, metric in enumerate(PUMP_SENSOR_METRICS):
            key = f"pump_{pump}_{metric}"
            if sensor_config := config.get(key):
                entity = await sensor.new_sensor(sensor_config)
                cg.add(var.set_pump_sensor(pump_index, metric_index, entity))

    for index, key in enumerate(BINARY_SENSORS):
        if binary_config := config.get(key):
            entity = await binary_sensor.new_binary_sensor(binary_config)
            cg.add(var.set_binary_sensor(index, entity))

    for index, key in enumerate(TEXT_SENSORS):
        if text_config := config.get(key):
            entity = await text_sensor.new_text_sensor(text_config)
            cg.add(var.set_text_sensor(index, entity))
