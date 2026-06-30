##############################################################################
#
# sael.py is a collection of WeeWX Services, assembled by Ian Millard,
# which generate realtime data in various formats used by the weewx-sael
# skin template.
# 
# The services are listed below.
#
##############################################################################
#
# airdensity.py and vpd.py
#
# Copyright (c) 2024 Sean Balfour <seanbalfourdresden@googlemail.com>
#
##############################################################################
# 
# Other services by Ian Millard
#
##############################################################################
"""
sael.py

A WeeWX extension for the weewx-Sael dashboard template.

"""
# --------------------------------------------------------
# WeeWX Live JSON Feed Service — version 3.2.6
# © 2025 Ian Millard, Sean Balfour — GPLv3
#
# Features:
#  • Configurable preferred output unit system (US / METRIC / METRICWX)
#  • Automatic numeric conversion, skipping non-physical fields
#  • Per-update conversion stats (converted / skipped)
#  • Cumulative totals persisted across restarts in .stats file
#  • Startup warning if no loop packets received within 20 s
#  • Journald integration for WeeWX 5.x
#  • Uptime seconds since service start included in metadata
#  • Optional MQTT publishing of live data
#  • Calculated variables: wind cardinal, beaufort, apparent temp, heat index, humidex, windchill, cloud base
# --------------------------------------------------------

import json
import os
import time
import threading
import logging
import datetime
import math
import weewx
from weewx.engine import StdService
from weeutil.weeutil import to_bool, to_int
from weewx.units import to_std_system, obs_group_dict

log = logging.getLogger("user.LiveDataService")

if weewx.__version__ < "5":
    raise weewx.UnsupportedFeature("This service requires WeeWX 5.2 or later")

# Try to import ephem for sunrise/sunset calculations
EPHEM_AVAILABLE = False
ASTRAL_MODE = False

try:
    import ephem
    EPHEM_AVAILABLE = True
    log.info("ephem library available for sunrise/sunset calculations")
except ImportError:
    try:
        # Try importing the newer version (some systems may have this)
        import astral
        from astral.sun import sun
        from astral import LocationInfo
        EPHEM_AVAILABLE = True
        ASTRAL_MODE = True
        log.info("astral library available for sunrise/sunset calculations")
    except ImportError:
        EPHEM_AVAILABLE = False
        ASTRAL_MODE = False
        log.warning("Neither ephem nor astral library available. isDay feature disabled.")

# Try to import paho-mqtt for MQTT publishing
MQTT_AVAILABLE = False
try:
    import paho.mqtt.client as mqtt
    MQTT_AVAILABLE = True
    log.info("paho-mqtt library available for MQTT publishing")
except ImportError:
    MQTT_AVAILABLE = False
    log.warning("paho-mqtt library not available. MQTT publishing disabled.")


class LiveDataService(StdService):
    SYSTEM_FIELDS = {'dateTime', 'usUnits', 'interval', 'rxCheckPercent'}
    NON_PHYSICAL_FIELDS = (
        'battery', 'voltage', 'signal', 'rssi', 'tx', 'rx',
        'pm', 'aqi', 'voc', 'co2', 'lightning', 'strike', 'count',
        'vpd'  # VPD is always in kPa, don't convert
    )
    
    # Wind direction mappings
    WIND_DIRECTIONS = [
        'North', 'NNE', 'NE', 'ENE', 'East', 'ESE', 'SE', 'SSE',
        'South', 'SSW', 'SW', 'WSW', 'West', 'WNW', 'NW', 'NNW'
    ]
    
    # Beaufort scale descriptions
    BEAUFORT_DESCRIPTIONS = [
        'Calm', 'Light air', 'Light breeze', 'Gentle breeze',
        'Moderate breeze', 'Fresh breeze', 'Strong breeze', 'Near gale',
        'Gale', 'Strong gale', 'Storm', 'Violent storm', 'Hurricane'
    ]

    # Beaufort scale descriptions
    BEAUFORT_COLORS = [
        '#85a3aa', '#7e98bb', '#6e90d0', '#0f94a7',
        '#39a239', '#c2863e', '#c8420d', '#d20032',
        '#af5088', '#754a92', '#45698d', '#c1fc77', '#f1ff6c'
    ]

    def __init__(self, engine, config_dict):
        super().__init__(engine, config_dict)
        cfg = config_dict.get('LiveData', {})

        # Core config
        self.json_file = cfg.get('json_file', '/srv/http/html/sael/jsondata/live.json')
        self.stats_file = f"{self.json_file}.stats"
        self.update_interval = to_int(cfg.get('update_interval', 2))
        self.include_archive = to_bool(cfg.get('include_archive', False))
        self.pretty_print = to_bool(cfg.get('pretty_print', True))
        self.flat_structure = to_bool(cfg.get('flat_structure', False))
        self.max_field_log = to_int(cfg.get('max_field_log', 500))

        # Preferred unit system
        self.unit_system_str = str(cfg.get('unit_system', 'US')).upper().strip()
        self.unit_map = {'US': weewx.US, 'METRIC': weewx.METRIC, 'METRICWX': weewx.METRICWX}
        self.unit_system = self.unit_map.get(self.unit_system_str, weewx.US)

        # MQTT Configuration
        self.mqtt_enabled = to_bool(cfg.get('mqtt_enabled', False))
        self.mqtt_client = None
        self.mqtt_config = {}
        
        if self.mqtt_enabled and MQTT_AVAILABLE:
            mqtt_cfg = cfg.get('MQTT', {})
            self.mqtt_config = {
                'host': mqtt_cfg.get('host', 'localhost'),
                'port': to_int(mqtt_cfg.get('port', 1883)),
                'username': mqtt_cfg.get('username'),
                'password': mqtt_cfg.get('password'),
                'topic': mqtt_cfg.get('topic', 'weather/live'),
                'qos': to_int(mqtt_cfg.get('qos', 0)),
                'retain': to_bool(mqtt_cfg.get('retain', False)),
                'client_id': mqtt_cfg.get('client_id', 'weewx_livedata'),
                'publish_format': mqtt_cfg.get('publish_format', 'json'),  # 'json' or 'individual'
                'individual_topic_prefix': mqtt_cfg.get('individual_topic_prefix', 'weather/')
            }
            log.info(f"MQTT publishing enabled: {self.mqtt_config['host']}:{self.mqtt_config['port']}, topic: {self.mqtt_config['topic']}")
            self._init_mqtt()
        elif self.mqtt_enabled and not MQTT_AVAILABLE:
            log.error("MQTT enabled but paho-mqtt library not installed. Install with: pip install paho-mqtt")
            self.mqtt_enabled = False

        # Station location for sunrise/sunset calculations
        self.station_config = config_dict.get('Station', {})
        
        # Parse latitude - handle string or numeric
        lat_val = self.station_config.get('latitude', 0.0)
        if isinstance(lat_val, (list, tuple)):
            self.latitude = float(lat_val[0])
        else:
            self.latitude = float(lat_val)
        
        # Parse longitude - handle string or numeric
        lon_val = self.station_config.get('longitude', 0.0)
        if isinstance(lon_val, (list, tuple)):
            self.longitude = float(lon_val[0])
        else:
            self.longitude = float(lon_val)
        
        # Parse altitude - handle list format [value, unit] or simple value
        alt_val = self.station_config.get('altitude', 0.0)
        if isinstance(alt_val, (list, tuple)):
            self.altitude = float(alt_val[0])
        else:
            self.altitude = float(alt_val)
        
        # Parse timezone offset
        tz_val = self.station_config.get('tz_offset', 0)
        if isinstance(tz_val, (list, tuple)):
            self.timezone = int(tz_val[0])
        else:
            self.timezone = int(tz_val)
        
        log.info(f"Station location: lat={self.latitude}, lon={self.longitude}, alt={self.altitude}m, tz={self.timezone}")
        
        # Check if we have a valid location
        if self.latitude == 0.0 and self.longitude == 0.0:
            log.warning("Station location not configured or at 0,0. isDay calculations may be inaccurate.")
        
        # Initialize sunrise/sunset calculator
        self.sun_calculator = None
        self.astral_mode = ASTRAL_MODE
        
        if EPHEM_AVAILABLE:
            if self.astral_mode:
                self.sun_calculator = self._init_astral()
            else:
                self.sun_calculator = self._init_ephem()
        else:
            log.warning("Sunrise/sunset calculations disabled. Install ephem or astral library.")

        # Runtime state
        self.data_cache = {}
        self.known_fields = set()
        self.cache_lock = threading.Lock()
        self.last_write = 0
        self.received_packet = False
        self.last_conv_stats = (0, 0)
        self.total_conv_stats = self._load_persistent_stats()  # persistent totals
        self.last_sun_calc_date = None  # Cache sunrise/sunset for current day
        self.cached_sunrise = None
        self.cached_sunset = None

        # Uptime / restart
        self.start_time = time.time()
        self.restart_time_iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

        # Persistence
        self.field_file = f"{self.json_file}.fields"
        self._load_known_fields()
        self._initialize_json_file()

        # Bind handlers
        self.bind(weewx.NEW_LOOP_PACKET, self.new_loop_packet)
        if self.include_archive:
            self.bind(weewx.NEW_ARCHIVE_RECORD, self.new_archive_record)

        log.info(f"LiveDataService ready — unit system: {self.unit_system_str}, "
                 f"output: {self.json_file}, interval: {self.update_interval}s")
        threading.Timer(20.0, self._check_first_packet).start()

    # ---------------------------------------------------------------------
    # MQTT Initialization
    # ---------------------------------------------------------------------

    def _init_mqtt(self):
        """Initialize MQTT client connection."""
        if not self.mqtt_enabled or not MQTT_AVAILABLE:
            return
            
        try:
            self.mqtt_client = mqtt.Client(client_id=self.mqtt_config['client_id'])
            
            # Set username/password if provided
            if self.mqtt_config.get('username'):
                self.mqtt_client.username_pw_set(
                    self.mqtt_config['username'],
                    self.mqtt_config.get('password')
                )
            
            # Set up callbacks
            self.mqtt_client.on_connect = self._on_mqtt_connect
            self.mqtt_client.on_disconnect = self._on_mqtt_disconnect
            
            # Connect to broker
            self.mqtt_client.connect(
                self.mqtt_config['host'],
                self.mqtt_config['port'],
                keepalive=60
            )
            
            # Start the network loop in a separate thread
            self.mqtt_client.loop_start()
            log.info("MQTT client initialized and connecting...")
            
        except Exception as e:
            log.error(f"Failed to initialize MQTT client: {e}")
            self.mqtt_enabled = False

    def _on_mqtt_connect(self, client, userdata, flags, rc):
        """Callback for MQTT connection."""
        if rc == 0:
            log.info(f"MQTT connected successfully to {self.mqtt_config['host']}:{self.mqtt_config['port']}")
        else:
            log.error(f"MQTT connection failed with code {rc}")
            self.mqtt_enabled = False

    def _on_mqtt_disconnect(self, client, userdata, rc):
        """Callback for MQTT disconnection."""
        log.warning(f"MQTT disconnected (rc={rc})")
        # Try to reconnect
        try:
            client.reconnect()
        except:
            pass

    def _publish_to_mqtt(self, data):
        """Publish data to MQTT broker."""
        if not self.mqtt_enabled or not self.mqtt_client:
            return
            
        try:
            if self.mqtt_config['publish_format'] == 'json':
                # Publish entire JSON object as single message
                payload = json.dumps(data, indent=None)
                result = self.mqtt_client.publish(
                    self.mqtt_config['topic'],
                    payload,
                    qos=self.mqtt_config['qos'],
                    retain=self.mqtt_config['retain']
                )
                if result.rc == mqtt.MQTT_ERR_SUCCESS:
                    log.debug(f"Published to MQTT topic: {self.mqtt_config['topic']}")
                else:
                    log.error(f"MQTT publish failed: {result.rc}")
                    
            elif self.mqtt_config['publish_format'] == 'individual':
                # Publish each observation as individual topic
                observations = data.get('observations', {})
                for field, value in observations.items():
                    # Skip complex values
                    if isinstance(value, (dict, list)):
                        continue
                    
                    topic = f"{self.mqtt_config['individual_topic_prefix']}{field}"
                    payload = str(value)
                    
                    result = self.mqtt_client.publish(
                        topic,
                        payload,
                        qos=self.mqtt_config['qos'],
                        retain=self.mqtt_config['retain']
                    )
                    
                # Also publish metadata as JSON
                metadata_topic = f"{self.mqtt_config['individual_topic_prefix']}metadata"
                metadata_payload = json.dumps(data.get('metadata', {}), indent=None)
                self.mqtt_client.publish(
                    metadata_topic,
                    metadata_payload,
                    qos=self.mqtt_config['qos'],
                    retain=self.mqtt_config['retain']
                )
                
                log.debug(f"Published {len(observations)} individual values to MQTT")
                
        except Exception as e:
            log.error(f"Error publishing to MQTT: {e}")

    # ---------------------------------------------------------------------
    # Sunrise/Sunset calculation initialization
    # ---------------------------------------------------------------------

    def _init_ephem(self):
        """Initialize ephem observer for sunrise/sunset calculations."""
        try:
            observer = ephem.Observer()
            observer.lat = str(self.latitude)
            observer.lon = str(self.longitude)
            observer.elevation = self.altitude
            log.debug("ephem observer initialized for sunrise/sunset calculations")
            return observer
        except Exception as e:
            log.error(f"Failed to initialize ephem: {e}")
            return None

    def _init_astral(self):
        """Initialize astral location for sunrise/sunset calculations."""
        try:
            # Create a location object
            # Note: astral uses meters for elevation
            location = LocationInfo(
                name="Weather Station",
                region="Local",
                timezone="UTC",  # We'll handle timezone offset manually
                latitude=self.latitude,
                longitude=self.longitude
            )
            log.debug("astral location initialized for sunrise/sunset calculations")
            return location
        except Exception as e:
            log.error(f"Failed to initialize astral: {e}")
            return None

    def _calculate_sunrise_sunset_ephem(self, date):
        """Calculate sunrise and sunset times using ephem."""
        try:
            self.sun_calculator.date = date
            sunrise = self.sun_calculator.previous_rising(ephem.Sun())
            sunset = self.sun_calculator.next_setting(ephem.Sun())
            
            # Convert to Unix timestamps
            sunrise_ts = time.mktime(sunrise.datetime().timetuple())
            sunset_ts = time.mktime(sunset.datetime().timetuple())
            
            # Adjust for timezone (ephem works in UTC)
            sunrise_ts += self.timezone * 3600
            sunset_ts += self.timezone * 3600
            
            return sunrise_ts, sunset_ts
        except Exception as e:
            log.error(f"Error calculating sunrise/sunset with ephem: {e}")
            return None, None

    def _calculate_sunrise_sunset_astral(self, date):
        """Calculate sunrise and sunset times using astral."""
        try:
            # Convert date to datetime at noon (astral expects datetime)
            dt = datetime.datetime(date.year, date.month, date.day, 12, 0, 0)
            
            # Calculate sun times
            s = sun(self.sun_calculator.observer, date=dt, tzinfo=datetime.timezone.utc)
            
            # Convert to Unix timestamps
            sunrise_ts = time.mktime(s['sunrise'].timetuple())
            sunset_ts = time.mktime(s['sunset'].timetuple())
            
            # Adjust for timezone
            sunrise_ts += self.timezone * 3600
            sunset_ts += self.timezone * 3600
            
            return sunrise_ts, sunset_ts
        except Exception as e:
            log.error(f"Error calculating sunrise/sunset with astral: {e}")
            return None, None

    def _get_sunrise_sunset_for_date(self, date):
        """Get sunrise and sunset times for a given date, with caching."""
        # Check if we already calculated for this date
        if (self.last_sun_calc_date and 
            self.last_sun_calc_date.year == date.year and
            self.last_sun_calc_date.month == date.month and
            self.last_sun_calc_date.day == date.day and
            self.cached_sunrise and self.cached_sunset):
            return self.cached_sunrise, self.cached_sunset
        
        # Calculate fresh
        if self.sun_calculator:
            if self.astral_mode:
                sunrise_ts, sunset_ts = self._calculate_sunrise_sunset_astral(date)
            else:
                sunrise_ts, sunset_ts = self._calculate_sunrise_sunset_ephem(date)
            
            if sunrise_ts and sunset_ts:
                self.cached_sunrise = sunrise_ts
                self.cached_sunset = sunset_ts
                self.last_sun_calc_date = date
                log.debug(f"Calculated sunrise/sunset for {date}: sunrise={sunrise_ts}, sunset={sunset_ts}")
                return sunrise_ts, sunset_ts
        
        return None, None

    # ---------------------------------------------------------------------
    # Weather Calculations
    # ---------------------------------------------------------------------

    def _calculate_wind_direction_cardinal(self, degrees):
        """Convert wind direction in degrees to cardinal direction."""
        if degrees is None:
            return None
        
        # Normalize to 0-360
        degrees = degrees % 360
        
        # Calculate index (16 directions)
        index = int((degrees + 11.25) / 22.5) % 16
        return self.WIND_DIRECTIONS[index]

    def _calculate_beaufort_scale(self, wind_speed):
        """Calculate Beaufort scale from wind speed.
        Wind speed should be in the current unit system.
        Returns tuple: (beaufort_number, description)
        """
        if wind_speed is None:
            return None, None
        
        # Convert to knots for Beaufort calculation
        if self.unit_system == weewx.US:
            # mph to knots
            knots = wind_speed * 0.868976
        elif self.unit_system == weewx.METRICWX:
            # m/s to knots
            knots = wind_speed * 1.94384
        else:
            # km/h to knots
            knots = wind_speed * 0.539957
        
        # Beaufort scale thresholds in knots
        if knots < 1:
            beaufort = 0
        elif knots < 4:
            beaufort = 1
        elif knots < 7:
            beaufort = 2
        elif knots < 11:
            beaufort = 3
        elif knots < 17:
            beaufort = 4
        elif knots < 22:
            beaufort = 5
        elif knots < 28:
            beaufort = 6
        elif knots < 34:
            beaufort = 7
        elif knots < 41:
            beaufort = 8
        elif knots < 48:
            beaufort = 9
        elif knots < 56:
            beaufort = 10
        elif knots < 64:
            beaufort = 11
        else:
            beaufort = 12
        
        return beaufort, self.BEAUFORT_DESCRIPTIONS[beaufort]

    def _calculate_beaufort_colors(self, wind_speed):
        """Calculate Beaufort scale from wind speed.
        Wind speed should be in the current unit system.
        Returns tuple: (beaufort_number, description)
        """
        if wind_speed is None:
            return None, None
        
        # Convert to knots for Beaufort calculation
        if self.unit_system == weewx.US:
            # mph to knots
            knots = wind_speed * 0.868976
        elif self.unit_system == weewx.METRICWX:
            # m/s to knots
            knots = wind_speed * 1.94384
        else:
            # km/h to knots
            knots = wind_speed * 0.539957
        
        # Beaufort scale thresholds in knots
        if knots < 1:
            beaufort = 0
        elif knots < 4:
            beaufort = 1
        elif knots < 7:
            beaufort = 2
        elif knots < 11:
            beaufort = 3
        elif knots < 17:
            beaufort = 4
        elif knots < 22:
            beaufort = 5
        elif knots < 28:
            beaufort = 6
        elif knots < 34:
            beaufort = 7
        elif knots < 41:
            beaufort = 8
        elif knots < 48:
            beaufort = 9
        elif knots < 56:
            beaufort = 10
        elif knots < 64:
            beaufort = 11
        else:
            beaufort = 12
        
        return beaufort, self.BEAUFORT_COLORS[beaufort]

    def _calculate_barometer_color(self, barometer):
        """Return a color hex string based on barometer value (hPa / mbar).
        Thresholds are upper-bound inclusive, evaluated low-to-high.
        Returns default amber if value is above all thresholds, grey if None.
        """
        if barometer is None:
            return '#808080'  # grey

        # Convert inHg → hPa when running in US unit system
        if self.unit_system == weewx.US:
            baro_hpa = barometer * 33.8639
        else:
            baro_hpa = barometer

        if baro_hpa <= 969.0:
            return '#ff00ff'
        elif baro_hpa <= 989.0:
            return '#f8d747'
        elif baro_hpa <= 1009.0:
            return '#007fff'
        elif baro_hpa <= 1029.0:
            return '#2e8b57'
        elif baro_hpa <= 1059.0:
            return '#ff6347'
        else:
            return '#eba141'  # default

    def _calculate_temperature_color(self, temp):
        """Return a color hex string based on temperature value.
        Thresholds are defined in Celsius, upper-bound inclusive.
        Returns lightgray if None.
        """
        if temp is None:
            return '#d3d3d3'  # lightgray

        # Normalise to Celsius for threshold comparison
        if self.unit_system == weewx.US:
            temp_c = (temp - 32) * 5 / 9
        else:
            temp_c = temp

        if temp_c <= -10:
            return '#8781bd'
        elif temp_c <= 0:
            return '#487ea9'
        elif temp_c <= 5:
            return '#369cac'
        elif temp_c <= 10:
            return '#9aba2f'
        elif temp_c <= 20:
            return '#eba141'
        elif temp_c <= 25:
            return '#ec5a34'
        elif temp_c <= 30:
            return '#d05f2d'
        elif temp_c <= 35:
            return '#d65b4a'
        elif temp_c <= 40:
            return '#dc4953'
        else:
            return '#e26870'

    def _calculate_wind_color(self, wind_speed):
        """Return a color hex string based on wind speed value.
        Thresholds are defined in m/s, upper-bound inclusive.
        Returns lightgray if None.
        """
        if wind_speed is None:
            return '#d3d3d3'  # lightgray

        # Normalise to m/s for threshold comparison
        if self.unit_system == weewx.US:
            wind_ms = wind_speed * 0.44704       # mph → m/s
        elif self.unit_system == weewx.METRIC:
            wind_ms = wind_speed / 3.6           # km/h → m/s
        else:
            wind_ms = wind_speed                 # METRICWX already m/s

        if wind_ms <= 1:
            return '#85a3aa'
        elif wind_ms <= 2:
            return '#7e98bb'
        elif wind_ms <= 3:
            return '#6e90d0'
        elif wind_ms <= 5:
            return '#0f94a7'
        elif wind_ms <= 8:
            return '#39a239'
        elif wind_ms <= 11:
            return '#c2863e'
        elif wind_ms <= 14:
            return '#c8420d'
        elif wind_ms <= 17:
            return '#d20032'
        elif wind_ms <= 21:
            return '#af5088'
        elif wind_ms <= 24:
            return '#754a92'
        elif wind_ms <= 28:
            return '#45698d'
        elif wind_ms <= 32:
            return '#c1fc77'
        else:
            return '#f1ff6c'

    def _calculate_rain_color(self, rain):
        """Return a color hex string based on rain/hail accumulation or rate (mm or mm/hr).
        Thresholds are upper-bound inclusive.
        Returns dark background color for None or zero, default amber above all thresholds.
        """
        if rain is None:
            return '#3a3d40'

        # Convert inches → mm when running in US unit system
        if self.unit_system == weewx.US:
            rain_mm = rain * 25.4
        else:
            rain_mm = rain

        if rain_mm <= 0:
            return '#3a3d40'
        elif rain_mm <= 1:
            return '#83818e'
        elif rain_mm <= 5:
            return '#615884'
        elif rain_mm <= 10:
            return '#34758e'
        elif rain_mm <= 30:
            return '#0b8c88'
        elif rain_mm <= 40:
            return '#359f35'
        elif rain_mm <= 80:
            return '#a79d51'
        elif rain_mm <= 120:
            return '#9f7f3a'
        elif rain_mm <= 250:
            return '#be4c07'
        elif rain_mm <= 500:
            return '#cf2848'
        elif rain_mm <= 750:
            return '#af5088'
        elif rain_mm <= 1000:
            return '#d476a3'
        elif rain_mm <= 1500:
            return '#fa9dbe'
        elif rain_mm <= 2000:
            return '#dcdcdc'
        else:
            return '#eba141'  # default

    def _calculate_uv_color(self, uv):
        """Return a color hex string based on UV index value.
        UV index is dimensionless — no unit conversion required.
        Thresholds are upper-bound inclusive.
        Returns grey for None, zero, or above all thresholds (default).
        """
        if uv is None:
            return 'grey'

        if uv <= 0:
            return 'grey'
        elif uv <= 2.9:
            return '#6fc77b'
        elif uv <= 5.9:
            return '#fed42d'
        elif uv <= 7.9:
            return '#fd8620'
        elif uv <= 10.9:
            return '#fb1215'
        elif uv <= 20:
            return '#de257b'
        else:
            return 'grey'  # default

    def _calculate_radiation_color(self, radiation):
        """Return a color hex string based on solar radiation value (W/m²).
        W/m² is unit-system independent — no conversion required.
        Thresholds are upper-bound inclusive.
        Returns grey for None, zero, or above all thresholds (default).
        """
        if radiation is None:
            return 'grey'

        if radiation <= 0:
            return 'grey'
        elif radiation <= 300:
            return '#f9de8a'
        elif radiation <= 600:
            return '#ffc367'
        elif radiation <= 900:
            return '#ffa242'
        elif radiation <= 1200:
            return '#fd8b17'
        elif radiation <= 1500:
            return '#ff7400'
        else:
            return 'grey'  # default

    def _calculate_apparent_temperature(self, temp, humidity, wind_speed):
        """Calculate Australian Apparent Temperature (Steadman 1984)."""
        if temp is None or humidity is None or wind_speed is None:
            return None
        
        # Convert to Celsius and m/s for calculation
        if self.unit_system == weewx.US:
            temp_c = (temp - 32) * 5/9
            wind_ms = wind_speed * 0.44704  # mph to m/s
        elif self.unit_system == weewx.METRIC:
            temp_c = temp
            wind_ms = wind_speed / 3.6      # km/h to m/s
        elif self.unit_system == weewx.METRICWX:
            temp_c = temp
            wind_ms = wind_speed            # m/s already
        else:
            # Fallback for unexpected unit systems
            temp_c = temp
            wind_ms = wind_speed
        
        # Water vapor pressure (hPa) using Magnus-Tetens approximation
        e = (humidity / 100) * 6.105 * math.exp(17.27 * temp_c / (237.7 + temp_c))
        
        # Apparent temperature formula (Australian BOM)
        at_c = temp_c + 0.33 * e - 0.7 * wind_ms - 4.0
        
        # Convert back to original unit system
        if self.unit_system == weewx.US:
            return at_c * 9/5 + 32
        else:
            return at_c

    def _calculate_heat_index(self, temp, humidity):
        """Calculate Heat Index (US NWS / Rothfusz formula)."""
        if temp is None or humidity is None:
            return None
        
        # Convert to Fahrenheit for calculation
        if self.unit_system == weewx.US:
            temp_f = temp
        else:
            temp_f = temp * 9/5 + 32
        
        # Heat Index is generally not calculated below 80°F
        # Returning the actual temperature is better than None for "live data"
        if temp_f < 80:
            return temp

        # Rothfusz regression
        hi = (-42.379 + 2.04901523 * temp_f + 10.14333127 * humidity - 
              0.22475541 * temp_f * humidity - 6.83783e-3 * temp_f**2 - 
              5.481717e-2 * humidity**2 + 1.22874e-3 * temp_f**2 * humidity + 
              8.5282e-4 * temp_f * humidity**2 - 1.99e-6 * temp_f**2 * humidity**2)
        
        # Adjustments
        if humidity < 13 and 80 <= temp_f <= 112:
            hi -= ((13 - humidity) / 4) * math.sqrt((17 - abs(temp_f - 95)) / 17)
        elif humidity > 85 and 80 <= temp_f <= 87:
            hi += ((humidity - 85) / 10) * ((87 - temp_f) / 5)
        
        # Convert back
        if self.unit_system == weewx.US:
            return hi
        else:
            return (hi - 32) * 5/9

    def _calculate_humidex(self, temp, humidity):
        """Calculate Canadian Humidex."""
        if temp is None or humidity is None:
            return None
        
        # Convert to Celsius for calculation
        if self.unit_system == weewx.US:
            temp_c = (temp - 32) * 5/9
        else:
            temp_c = temp
        
        # Dewpoint calculation (Magnus-Tetens)
        a = 17.27
        b = 237.7
        # Ensure humidity is not 0 to avoid math.log error
        rh = max(humidity, 0.1)
        alpha = ((a * temp_c) / (b + temp_c)) + math.log(rh / 100)
        dewpoint = (b * alpha) / (a - alpha)
        
        # Vapor pressure calculation (hPa)
        # Using 5417.7530 as the standard Canadian constant
        e = 6.11 * math.exp(5417.7530 * ((1/273.16) - (1/(dewpoint + 273.16))))
        
        # Humidex increment
        h = 0.5555 * (e - 10.0)
        humidex_c = temp_c + h
        
        # Check if we should return the calculated value or just the temp
        # Environment Canada typically only reports Humidex if it is >= 1 higher than temp
        if (humidex_c - temp_c) < 1.0:
            result_c = temp_c
        else:
            result_c = humidex_c
        
        # Convert back to current unit system
        if self.unit_system == weewx.US:
            return result_c * 9/5 + 32
        else:
            return result_c

    def _calculate_windchill(self, temp, wind_speed):
        """Calculate Wind Chill (US/Canadian formula)."""
        if temp is None or wind_speed is None:
            return None
        
        # Convert to Fahrenheit and mph for calculation
        if self.unit_system == weewx.US:
            temp_f = temp
            wind_mph = wind_speed
        elif self.unit_system == weewx.METRICWX:
            temp_f = temp * 9/5 + 32
            wind_mph = wind_speed * 2.23694  # m/s to mph
        else:
            # Assume METRIC (km/h)
            temp_f = temp * 9/5 + 32
            wind_mph = wind_speed * 0.621371  # km/h to mph
        
        # Wind chill is only defined for temps <= 50°F and wind > 3 mph
        if temp_f > 50 or wind_mph <= 3:
            result_f = temp_f
        else:
            # US/Canadian wind chill formula
            result_f = (35.74 + 
                        0.6215 * temp_f - 
                        35.75 * (wind_mph ** 0.16) + 
                        0.4275 * temp_f * (wind_mph ** 0.16))
        
        # Convert back to current unit system
        if self.unit_system == weewx.US:
            return result_f
        else:
            return (result_f - 32) * 5/9

    def _calculate_vpd(self, temp, humidity):
        """Calculate Vapor Pressure Deficit (VPD) in kPa."""
        if temp is None or humidity is None:
            return None
        
        # Convert to Celsius for calculation
        if self.unit_system == weewx.US:
            temp_c = (temp - 32) * 5/9
        else:
            temp_c = temp
        
        # Constrain humidity between 0 and 100
        rh = max(min(humidity, 100.0), 0.0)
        
        # Saturation vapor pressure (SVP) in kPa (Buck 1981)
        svp = 0.61121 * math.exp((18.678 - temp_c / 234.5) * (temp_c / (257.14 + temp_c)))
        
        # VPD = SVP * (1 - RH/100)
        # This is mathematically identical to (SVP - AVP)
        vpd = svp * (1.0 - (rh / 100.0))
        
        return vpd

        # weewx.units.obs_group_dict['wetbulbTemp'] = 'group_temperature'
    def _calculate_wet_bulb_temp(self, temp, humidity):
        if temp is None or humidity is None:
            return None
    
        # Ensure humidity is not negative (math safety)
        H = max(0.0, float(humidity))
    
        # Convert to Celsius: Roland Stull formula requires Celsius and 0-100 humidity
        if self.unit_system == weewx.US:
            T = (temp - 32.0) * 5.0/9.0
        else:
            T = float(temp)

        # Roland Stull Formula
        # Result is in Celsius
        wbt_c = (
                T * math.atan(0.151977 * math.sqrt(H + 8.313659)) 
                + math.atan(T + H) 
                - math.atan(H - 1.676331) 
                + 0.00391838 * math.pow(H, 1.5) * math.atan(0.023101 * H) 
                - 4.686035
        )
    
        # Return in original unit system
        if self.unit_system == weewx.US:
            return wbt_c * 9.0/5.0 + 32.0
        return wbt_c

    def _calculate_cloud_base(self, temp, dewpoint):
        """Calculate estimated cloud base height (Hennig formula) AMSL."""
        if temp is None or dewpoint is None:
            return None

        # 1. Access the station altitude from the config
        # Note: WeeWX stores this as a tuple (value, "unit") e.g., (120.0, "meter")
        stn_alt_tuple = self.config_dict['Station'].get('altitude')
        if stn_alt_tuple is None:
            stn_alt_val = 0.0
        else:
            # Convert tuple to float (e.g., "120.0")
            stn_alt_val = float(stn_alt_tuple[0])

        # 2. Convert units for calculation (Formula constant 4.4 is F-based)
        if self.unit_system == weewx.US:
            temp_f = temp
            dewpoint_f = dewpoint
        else:
            temp_f = (temp * 9/5) + 32
            dewpoint_f = (dewpoint * 9/5) + 32

        # 3. Calculate spread
        spread_f = temp_f - dewpoint_f
        if spread_f < 0:
            spread_f = 0
    
        # 4. Hennig formula: Result in Feet (Height Above Ground Level)
        cloud_base_agl_ft = (spread_f / 4.4) * 1000

        # 5. Add Station Altitude and Convert Back
        if self.unit_system == weewx.US:
            # If config is in meters but system is US, convert altitude first
            if stn_alt_tuple[1] == 'meter':
                stn_alt_val *= 3.28084
            return cloud_base_agl_ft + stn_alt_val
        else:
            # Convert Feet to Meters for AGL
            cloud_base_agl_m = cloud_base_agl_ft * 0.3048
            # If config is in feet but system is Metric, convert altitude first
            if stn_alt_tuple[1] == 'foot':
                stn_alt_val *= 0.3048
            return cloud_base_agl_m + stn_alt_val

    # Helper function to get Dewpoint and Cloud Base data
    def _calculate_dewpoint(self, temp, humidity):
        # Convert to Celsius for standard formula
        if self.unit_system == weewx.US:
            t = (temp - 32) * 5/9
        else:
            t = temp
            
        rh = max(humidity, 0.1)
        # Magnus-Tetens Formula
        a, b = 17.27, 237.7
        alpha = ((a * t) / (b + t)) + math.log(rh / 100.0)
        dp_c = (b * alpha) / (a - alpha)
        
        # Return in original unit system
        if self.unit_system == weewx.US:
            return dp_c * 9/5 + 32
        return dp_c

    def new_loop_packet(self, event):
        # 1. Pull the raw radiation value
        solarRad = event.packet.get('radiation')
    
        # 2. Calculate Lux using your function
        if solarRad is not None:
            event.packet['lux'] = self._calculate_lux(solarRad)

    def _calculate_lux(self, solarRad):
        # Ensure value is float and handle None
        try:
            return float(solarRad) * 126.7
        except (TypeError, ValueError):
            return None

    def _add_calculated_values(self, packet):
        """Add all calculated weather values to the packet."""
        try:
            # Get base values (use converted values from cache if available)
            temp = packet.get('outTemp') or self.data_cache.get('outTemp')
            humidity = packet.get('outHumidity') or self.data_cache.get('outHumidity')
            wind_speed = packet.get('windSpeed') or self.data_cache.get('windSpeed')
            wind_gust = packet.get('windGust') or self.data_cache.get('windGust')
            wind_dir = packet.get('windDir') or self.data_cache.get('windDir')
            dewpoint = packet.get('dewpoint') or self.data_cache.get('dewpoint')
            solarRad = packet.get('radiation') or self.data_cache.get('radiation')
            
            # If dewpoint is missing, calculate it manually so Cloud Base works!
            if dewpoint is None and temp is not None and humidity is not None:
                dewpoint = self._calculate_dewpoint(temp, humidity)
            
            # Wind Cardinal Direction
            if wind_dir is not None:
                wind_cardinal = self._calculate_wind_direction_cardinal(wind_dir)
                if wind_cardinal:
                    self.data_cache['windCardinal'] = wind_cardinal
                    if 'windCardinal' not in self.known_fields:
                        self.known_fields.add('windCardinal')
                        log.info("New calculated field: windCardinal")
            
            # Beaufort Scale
            if wind_speed is not None:
                beaufort_num, beaufort_desc = self._calculate_beaufort_scale(wind_speed)
                if beaufort_num is not None:
                    self.data_cache['beaufortScale'] = beaufort_num
                    self.data_cache['beaufortDesc'] = beaufort_desc
                    if 'beaufortScale' not in self.known_fields:
                        self.known_fields.add('beaufortScale')
                        self.known_fields.add('beaufortDesc')
                        log.info("New calculated fields: beaufortScale, beaufortDesc")

            # Beaufort Colors wind speed
            if wind_speed is not None:
                beaufort_num, bft_colors = self._calculate_beaufort_colors(wind_speed)
                if beaufort_num is not None:
                    self.data_cache['beaufortScale'] = beaufort_num
                    self.data_cache['beaufortColorSpeed'] = bft_colors
                    if 'beaufortScale' not in self.known_fields:
                        self.known_fields.add('beaufortScale')
                        self.known_fields.add('beaufortColorSpeed')
                        log.info("New calculated fields: beaufortColorSpeed")

            # Beaufort Colors gust speed
            if wind_gust is not None:
                beaufort_num, bft_colors = self._calculate_beaufort_colors(wind_gust)
                if beaufort_num is not None:
                    self.data_cache['beaufortScale'] = beaufort_num
                    self.data_cache['beaufortColorGust'] = bft_colors
                    if 'beaufortScale' not in self.known_fields:
                        self.known_fields.add('beaufortScale')
                        self.known_fields.add('beaufortColorGust')
                        log.info("New calculated fields: beaufortColorGust")
            
            # Solar radiation color
            radiation = packet.get('radiation') or self.data_cache.get('radiation')
            self.data_cache['radiationColor'] = self._calculate_radiation_color(radiation)
            if 'radiationColor' not in self.known_fields:
                self.known_fields.add('radiationColor')
                log.info("New calculated field: radiationColor")

            # UV color
            uv = packet.get('UV') or self.data_cache.get('UV')
            self.data_cache['uvColor'] = self._calculate_uv_color(uv)
            if 'uvColor' not in self.known_fields:
                self.known_fields.add('uvColor')
                log.info("New calculated field: uvColor")

            # Rain / hail color fields
            for src_field, color_field in (
                ('rain',     'rainColor'),
                ('rainRate', 'rainRateColor'),
                ('hail',     'hailColor'),
                ('hailRate', 'hailRateColor'),
            ):
                val = packet.get(src_field) or self.data_cache.get(src_field)
                color = self._calculate_rain_color(val)
                self.data_cache[color_field] = color
                if color_field not in self.known_fields:
                    self.known_fields.add(color_field)
                    log.info(f"New calculated field: {color_field}")

            # Wind speed color
            if wind_speed is not None:
                self.data_cache['windSpeedColor'] = self._calculate_wind_color(wind_speed)
                if 'windSpeedColor' not in self.known_fields:
                    self.known_fields.add('windSpeedColor')
                    log.info("New calculated field: windSpeedColor")

            # Wind gust color
            if wind_gust is not None:
                self.data_cache['windGustColor'] = self._calculate_wind_color(wind_gust)
                if 'windGustColor' not in self.known_fields:
                    self.known_fields.add('windGustColor')
                    log.info("New calculated field: windGustColor")

            # Temperature group colors
            for src_field, color_field in (
                ('outTemp',      'outTempColor'),
                ('inTemp',       'inTempColor'),
                ('dewpoint',     'dewpointColor'),
                ('apparentTemp', 'apparentTempColor'),
                ('feelsLike',    'feelsLikeColor'),
                ('heatIndex',    'heatIndexColor'),
                ('humidex',      'humidexColor'),
                ('windChill',    'windChillColor'),
                ('wetbulbTemp',  'wetbulbTempColor'),
            ):
                val = packet.get(src_field) or self.data_cache.get(src_field)
                self.data_cache[color_field] = self._calculate_temperature_color(val)
                if color_field not in self.known_fields:
                    self.known_fields.add(color_field)
                    log.info(f"New calculated field: {color_field}")

            # Barometer color
            barometer = packet.get('barometer') or self.data_cache.get('barometer')
            if barometer is not None:
                baro_color = self._calculate_barometer_color(barometer)
                self.data_cache['barometerColor'] = baro_color
                if 'barometerColor' not in self.known_fields:
                    self.known_fields.add('barometerColor')
                    log.info("New calculated field: barometerColor")

            # Apparent Temperature
            if temp is not None and humidity is not None and wind_speed is not None:
                apparent_temp = self._calculate_apparent_temperature(temp, humidity, wind_speed)
                if apparent_temp is not None:
                    self.data_cache['apparentTemp'] = apparent_temp
                    if 'apparentTemp' not in self.known_fields:
                        self.known_fields.add('apparentTemp')
                        log.info("New calculated field: apparentTemp")
            
            # Heat Index
            if temp is not None and humidity is not None:
                heat_index = self._calculate_heat_index(temp, humidity)
                if heat_index is not None:
                    self.data_cache['heatIndex'] = round(heat_index, 1)
                    if 'heatIndex' not in self.known_fields:
                        self.known_fields.add('heatIndex')
                        log.info("New calculated field: heatIndex")
            
            # Humidex
            if temp is not None and humidity is not None:
                humidex = self._calculate_humidex(temp, humidity)
                if humidex is not None:
                    self.data_cache['humidex'] = round(humidex, 1)
                    if 'humidex' not in self.known_fields:
                        self.known_fields.add('humidex')
                        log.info("New calculated field: humidex")
            
            # Wind Chill
            if temp is not None and wind_speed is not None:
                windchill = self._calculate_windchill(temp, wind_speed)
                if windchill is not None:
                    self.data_cache['windChill'] = round(windchill, 1)
                    if 'windChill' not in self.known_fields:
                        self.known_fields.add('windChill')
                        log.info("New calculated field: windChill")
            
            # VPD (Vapor Pressure Deficit) - always in kPa
            if temp is not None and humidity is not None:
                vpd = self._calculate_vpd(temp, humidity)
                if vpd is not None:
                    self.data_cache['vpd'] = round(vpd, 2)
                    if 'vpd' not in self.known_fields:
                        self.known_fields.add('vpd')
                        log.info("New calculated field: vpd (kPa)")
            
            # Cloud Base
            if temp is not None and dewpoint is not None:
                cloud_base = self._calculate_cloud_base(temp, dewpoint)
                if cloud_base is not None:
                    self.data_cache['cloudBase'] = round(cloud_base, 0)
                    if 'cloudBase' not in self.known_fields:
                        self.known_fields.add('cloudBase')
                        unit = 'ft' if self.unit_system == weewx.US else 'm'
                        log.info(f"New calculated field: cloudBase ({unit})")
            
            # Dewpoint
            if temp is not None and dewpoint is not None:
                dewpoint = self._calculate_dewpoint(temp, humidity)
                if dewpoint is not None:
                    self.data_cache['dewpoint'] = round(dewpoint, 1)
                    if 'dewpoint' not in self.known_fields:
                        self.known_fields.add('dewpoint')
                        log.info(f"New calculated field: dewpoint")

            # Wetbulb Temperature
            if temp is not None and humidity is not None:
                wbt = self._calculate_wet_bulb_temp(temp, humidity)
                if wbt is not None:
                    self.data_cache['wetbulbTemp'] = wbt
                    if 'wetbulbTemp' not in self.known_fields:
                        self.known_fields.add('wetbulbTemp')
                        log.info("New calculated field: wetbulbTemp")

            # Illuminance
            if solarRad is not None:
                lux = self._calculate_lux(solarRad)
                if lux is not None:
                    self.data_cache['illuminance'] = lux
                    if 'illuminance' not in self.known_fields:
                        self.known_fields.add('illuminance')
                        log.info("New calculated field: illuminance")

        except Exception as e:
            log.error(f"Error calculating derived values: {e}", exc_info=True)

    # ---------------------------------------------------------------------
    # Packet handlers
    # ---------------------------------------------------------------------

    def new_loop_packet(self, event):
        self.received_packet = True
        packet, conv_stats = self._convert_packet_safely(event.packet)
        self._update_cumulative_stats(conv_stats)

        with self.cache_lock:
            for k, v in packet.items():
                if v is not None:
                    if k not in self.known_fields:
                        self.known_fields.add(k)
                        log.info(f"New field detected: {k}")
                        self._save_known_fields()
                    self.data_cache[k] = v

            # Add isDay observation based on sunrise/sunset (1 = day, 0 = night)
            self._add_day_observation(packet)
            
            # Add calculated weather values
            self._add_calculated_values(packet)
            
            if time.time() - self.last_write >= self.update_interval:
                self._update_json_file()
                self.last_write = time.time()

    def new_archive_record(self, event):
        record, _ = self._convert_packet_safely(event.record, archive=True)
        with self.cache_lock:
            for k, v in record.items():
                if v is not None and k not in self.SYSTEM_FIELDS:
                    ak = f"archive_{k}"
                    self.data_cache[ak] = v

    # ---------------------------------------------------------------------
    # Day/Night logic
    # ---------------------------------------------------------------------

    def _add_day_observation(self, packet):
        """Add isDay observation (1 = daytime, 0 = nighttime) based on sunrise/sunset times."""
        try:
            # Get current time from packet
            current_time_ts = packet.get('dateTime')
            if not current_time_ts:
                log.warning("No dateTime in packet for isDay calculation")
                return
            
            current_dt = datetime.datetime.fromtimestamp(current_time_ts)
            current_time = current_dt.time()  # Just the time part
            
            # Get sunrise and sunset times
            sunrise_ts = None
            sunset_ts = None
            
            # Check for sunrise/sunset in packet
            for field in ['sunrise', 'daySunrise', 'Sunrise']:
                if field in packet:
                    sunrise_ts = packet[field]
                    break
            
            for field in ['sunset', 'daySunset', 'Sunset']:
                if field in packet:
                    sunset_ts = packet[field]
                    break
            
            # If not found, try cache or calculate
            if sunrise_ts is None or sunset_ts is None:
                sunrise_ts = self.data_cache.get('archive_sunrise')
                sunset_ts = self.data_cache.get('archive_sunset')
            
            if (sunrise_ts is None or sunset_ts is None) and self.sun_calculator:
                current_date = current_dt.date()
                sunrise_ts, sunset_ts = self._get_sunrise_sunset_for_date(current_date)
            
            # If we have times, convert to time objects
            if sunrise_ts is not None and sunset_ts is not None:
                sunrise_dt = datetime.datetime.fromtimestamp(sunrise_ts)
                sunset_dt = datetime.datetime.fromtimestamp(sunset_ts)
                
                sunrise_time = sunrise_dt.time()
                sunset_time = sunset_dt.time()
                
                # Convert times to minutes since midnight for easier comparison
                current_minutes = current_time.hour * 60 + current_time.minute
                sunrise_minutes = sunrise_time.hour * 60 + sunrise_time.minute
                sunset_minutes = sunset_time.hour * 60 + sunset_time.minute
                
                log.debug(f"Time in minutes: current={current_minutes}, sunrise={sunrise_minutes}, sunset={sunset_minutes}")
                
                if sunrise_minutes < sunset_minutes:
                    # Normal day: sunrise before sunset
                    if sunrise_minutes <= current_minutes < sunset_minutes:
                        is_day_value = 1  # Daytime
                        log.debug(f"Normal daytime: {sunrise_time} <= {current_time} < {sunset_time}")
                    else:
                        is_day_value = 0  # Nighttime
                        log.debug(f"Normal nighttime: NOT ({sunrise_time} <= {current_time} < {sunset_time})")
                else:
                    # Sunset is before sunrise (crosses midnight)
                    # This means: sunset yesterday, sunrise today
                    if current_minutes >= sunrise_minutes or current_minutes < sunset_minutes:
                        is_day_value = 1  # Daytime (spans midnight)
                        log.debug(f"Cross-midnight daytime: {current_time} >= {sunrise_time} OR {current_time} < {sunset_time}")
                    else:
                        is_day_value = 0  # Nighttime (between sunset and sunrise)
                        log.debug(f"Cross-midnight nighttime: {sunset_time} <= {current_time} < {sunrise_time}")
                
                # Only log isDay changes at INFO level
                current_is_day = self.data_cache.get('isDay')
                if current_is_day != is_day_value:
                    log.info(f"isDay changed to {is_day_value} (current: {current_time}, sunrise: {sunrise_time}, sunset: {sunset_time})")
                else:
                    log.debug(f"isDay = {is_day_value} (current: {current_time}, sunrise: {sunrise_time}, sunset: {sunset_time})")
            else:
                # Fallback to time-based
                current_hour = current_dt.hour
                if 6 <= current_hour < 18:
                    is_day_value = 1
                else:
                    is_day_value = 0
                log.debug(f"Fallback isDay: {is_day_value} (hour: {current_hour})")
            
            self.data_cache['isDay'] = is_day_value
            
            if 'isDay' not in self.known_fields:
                self.known_fields.add('isDay')
                self._save_known_fields()
            
        except Exception as e:
            log.error(f"Error in isDay calculation: {e}", exc_info=True)
            # Default to nighttime
            self.data_cache['isDay'] = 0

    # ---------------------------------------------------------------------
    # Conversion logic
    # ---------------------------------------------------------------------

    def _convert_packet_safely(self, packet, archive=False):
        converted_count = skipped_count = 0
        try:
            current_us = packet.get('usUnits', weewx.US)
            if current_us == self.unit_system:
                return packet, (0, len(packet))

            convert_me, skip_me = {}, {}
            for k, v in packet.items():
                lname = k.lower()
                if any(x in lname for x in self.NON_PHYSICAL_FIELDS):
                    skip_me[k] = v
                    skipped_count += 1
                elif k in obs_group_dict or lname in obs_group_dict:
                    convert_me[k] = v
                else:
                    skip_me[k] = v
                    skipped_count += 1

            converted = {}
            if convert_me:
                converted = to_std_system(dict(convert_me, usUnits=current_us), self.unit_system)
                converted_count = len(convert_me)

            merged = {**converted, **skip_me}
            return merged, (converted_count, skipped_count)

        except Exception as e:
            log.error(f"Safe conversion error: {e}")
            return packet, (0, 0)

    # ---------------------------------------------------------------------
    # Cumulative stats handling
    # ---------------------------------------------------------------------

    def _update_cumulative_stats(self, conv_stats):
        """Update cumulative conversion statistics."""
        conv, skip = conv_stats
        self.last_conv_stats = conv_stats
        self.total_conv_stats[0] += conv
        self.total_conv_stats[1] += skip
        self._save_persistent_stats()

    # ---------------------------------------------------------------------
    # JSON writing and MQTT publishing
    # ---------------------------------------------------------------------

    def _update_json_file(self):
        try:
            # Create observations dictionary
            obs = {}
            for k, v in self.data_cache.items():
                if k not in self.SYSTEM_FIELDS and not k.startswith('_'):
                    obs[k] = v
            
            # Check if isDay is in observations (debug only)
            if 'isDay' in obs:
                log.debug(f"isDay = {obs['isDay']} written to JSON")

            conv_last, skip_last = self.last_conv_stats
            conv_total, skip_total = self.total_conv_stats

            data = {
                'updated': int(time.time()),
                'updated_iso': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                'observations': obs,
                'metadata': self._build_metadata(conv_last, skip_last, conv_total, skip_total)
            }

            # Write to JSON file
            tmp = f"{self.json_file}.tmp"
            with open(tmp, 'w') as f:
                json.dump(data, f, indent=2 if self.pretty_print else None, sort_keys=self.pretty_print)
            os.replace(tmp, self.json_file)

            # Only log every 10 updates or when debug is enabled
            if conv_last + skip_last > 0:
                log.info(f"Updated {os.path.basename(self.json_file)} — {len(obs)} fields")
            else:
                log.debug(f"Updated {os.path.basename(self.json_file)} — {len(obs)} fields ({conv_last} converted, {skip_last} skipped)")
            
            # Publish to MQTT if enabled
            if self.mqtt_enabled and self.mqtt_client:
                self._publish_to_mqtt(data)
                log.debug("Published to MQTT")

        except Exception as e:
            log.error(f"Error updating JSON file: {e}", exc_info=True)

    # ---------------------------------------------------------------------
    # Metadata and units
    # ---------------------------------------------------------------------

    def _build_metadata(self, conv_last, skip_last, conv_total, skip_total):
        metadata = {
            'service': 'WeeWX Live Data',
            'version': '3.2.6',
            'preferred_unit_system': self.unit_system_str,
            'field_count': len(self.known_fields),
            'restart_time': self.restart_time_iso,
            'uptime_seconds': int(time.time() - self.start_time),
            'units': self._get_unit_labels(),
            'calculated_fields': [
                'windCardinal', 'beaufortScale', 'beaufortDesc', 'beaufortColorSpeed', 'beaufortColorGust',
                'apparentTemp', 'heatIndex', 'humidex', 'windChill', 'vpd', 'cloudBase', 'dewpoint', 'wetbulbTemp',
                'barometerColor', 'outTempColor', 'inTempColor', 'dewpointColor',
                'apparentTempColor', 'feelsLikeColor', 'heatIndexColor',
                'humidexColor', 'windChillColor', 'wetbulbTempColor',
                'windSpeedColor', 'windGustColor',
                'rainColor', 'rainRateColor', 'hailColor', 'hailRateColor',
                'uvColor', 'radiationColor', 'illuminance'
            ],
            'stats': {
                'converted_last': conv_last,
                'skipped_last': skip_last,
                'converted_total': conv_total,
                'skipped_total': skip_total,
                'last_update': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            }
        }
        
        # Add MQTT info if enabled
        if self.mqtt_enabled:
            metadata['mqtt'] = {
                'enabled': True,
                'host': self.mqtt_config.get('host'),
                'topic': self.mqtt_config.get('topic'),
                'format': self.mqtt_config.get('publish_format')
            }
        
        return metadata

    def _get_unit_labels(self):
        if self.unit_system == weewx.US:
            return {'temperature': '°F', 'pressure': 'inHg', 'rain': 'in',
                    'wind': 'mph', 'radiation': 'W/m²', 'distance': 'mi', 'cloudBase': 'ft'}
        elif self.unit_system == weewx.METRIC:
            return {'temperature': '°C', 'pressure': 'mbar', 'rain': 'mm',
                    'wind': 'km/h', 'radiation': 'W/m²', 'distance': 'km', 'cloudBase': 'm'}
        else:
            return {'temperature': '°C', 'pressure': 'mbar', 'rain': 'mm',
                    'wind': 'm/s', 'radiation': 'W/m²', 'distance': 'km', 'cloudBase': 'm'}

    # ---------------------------------------------------------------------
    # Persistence helpers
    # ---------------------------------------------------------------------

    def _initialize_json_file(self):
        try:
            os.makedirs(os.path.dirname(self.json_file), exist_ok=True)
            with open(self.json_file, 'w') as f:
                json.dump({'observations': {}, 'metadata': {}}, f)
        except Exception as e:
            log.error(f"JSON init failed: {e}")

    def _load_known_fields(self):
        self.field_file = getattr(self, 'field_file', f"{self.json_file}.fields")
        if os.path.exists(self.field_file):
            try:
                with open(self.field_file) as f:
                    self.known_fields = set(json.load(f))
            except Exception:
                self.known_fields = set()

    def _save_known_fields(self):
        try:
            with open(self.field_file, 'w') as f:
                json.dump(sorted(list(self.known_fields)), f, indent=2)
        except Exception:
            pass

    # ---- persistent stats handling ----
    def _load_persistent_stats(self):
        if os.path.exists(self.stats_file):
            try:
                with open(self.stats_file) as f:
                    data = json.load(f)
                    return [int(data.get('converted_total', 0)), int(data.get('skipped_total', 0))]
            except Exception as e:
                log.error(f"Failed to load stats file: {e}")
        return [0, 0]

    def _save_persistent_stats(self):
        tmp = f"{self.stats_file}.tmp"
        try:
            data = {
                'converted_total': self.total_conv_stats[0],
                'skipped_total': self.total_conv_stats[1],
                'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            }
            with open(tmp, 'w') as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self.stats_file)
        except Exception as e:
            log.error(f"Failed to save stats file: {e}")

    # ---------------------------------------------------------------------
    # Safety / shutdown
    # ---------------------------------------------------------------------

    def _check_first_packet(self):
        if not self.received_packet:
            log.warning("⚠ No loop packets received within 20 s — check your driver configuration")

    def shutDown(self):
        log.info("Shutting down LiveDataService — writing final JSON + stats")
        with self.cache_lock:
            self._update_json_file()
            self._save_known_fields()
            self._save_persistent_stats()
        
        # Disconnect MQTT if connected
        if self.mqtt_enabled and self.mqtt_client:
            try:
                self.mqtt_client.loop_stop()
                self.mqtt_client.disconnect()
                log.info("MQTT client disconnected")
            except:
                pass
        
        log.info("LiveDataService stopped cleanly")

#!/usr/bin/env python3
"""
WeeWX Service: weatherapi.py
Polls various weather APIs and saves raw JSON responses to text files.

Installation:
1. Copy this file to your WeeWX user directory (e.g., /usr/share/weewx/user/ or ~/weewx-data/bin/user/)
2. Add configuration to weewx.conf (see example below)
3. Add 'user.weatherapi.WeatherAPIService' to the data_services list in [Engine][[Services]]

Example weewx.conf configuration:

[WeatherAPI]
    [[Forecast]]
        api_type = openmeteo
        enabled = True
        data_path = /var/lib/weewx/jsondata/forecastcard.txt
    
    [[Airquality]]
        api_type = airquality
        enabled = True
        data_path = /var/www/html/sael/jsondata/particles.txt
    
    [[Flood]]
        api_type = flood
        enabled = True
        data_path = /var/www/html/sael/jsondata/flood.txt
    
    [[AuroraWatch]]
        api_type = aurorawatch
        enabled = False  # Temporarily disabled
        data_path = /var/www/html/sael/jsondata/aurora.txt
    
    [[HeatAlert]]
        api_type = heatalert
        enabled = True
        location_code = E12000008
        data_path = /var/www/html/sael/jsondata/heat.txt
    
    [[MetOfficeRSS]]
        api_type = metofficerss
        region_code = se
        data_path = /var/www/html/sael/jsondata/metofficerss.txt
    
    [[Xweather]]
        api_type = xweather
        client_id = your_client_id
        client_secret = your_client_secret
        data_path = /var/www/html/sael/jsondata/xweather.txt
    
    [[Boltek]]
        api_type = boltek
        enabled = True
        data_path = /var/www/html/sael/jsondata/boltek.txt
    
    # Custom API example
    [[MyCustomAPI]]
        api_type = custom
        enabled = True
        url = https://api.example.com/data?format=json
        poll_interval = 600
        data_path = /var/www/html/sael/jsondata/custom.txt
    
    # Custom API with headers
    [[HomeAssistant]]
        api_type = custom
        enabled = True
        url = http://homeassistant.local:8123/api/states
        header = content-type: application/json|Authorization: Bearer YOUR_TOKEN_HERE
        poll_interval = 60
        data_path = /var/www/html/sael/jsondata/homeassistant.txt

Supported API types:
- openweather: OpenWeatherMap (900s, requires app_id)
- openmeteo: Open-Meteo Forecast (300s)
- airquality: Open-Meteo Air Quality (900s)
- metar: CheckWX METAR (3600s, requires X-API-Key and metar_name)
- earthquakes: EMSC Earthquakes (3600s)
- ki: NOAA K-Index Forecast (21600s)
- k2: NOAA Geospace DST (43200s)
- flood: UK Environment Agency Flood Warnings (900s)
- aurorawatch: AuroraWatch UK (180s)
- ovation: NOAA OVATION Aurora (300s)
- heatalert: UKHSA Heat Alert (1800s, requires location_code)
- coldalert: UKHSA Cold Alert (1800s, requires location_code)
- metofficerss: Met Office Warnings RSS (300s, requires region_code)
- xweather: Xweather Forecast (300s, requires client_id and client_secret)
- boltek: Boltek NGX Lightning Detector (60s)
- custom: User-defined API (requires url and poll_interval)

"""

import os
import time
import requests
import threading
from typing import Dict, Any, Optional

import weewx
from weewx.engine import StdService
from weeutil.weeutil import timestamp_to_string

# WeeWX 5.x logging
try:
    import weeutil.logger
    import logging
    log = logging.getLogger(__name__)
except ImportError:
    import syslog
    log = None


def logmsg(level, msg):
    """Unified logging function for WeeWX 4.x and 5.x compatibility"""
    if log:
        log.log(level, msg)
    else:
        syslog.syslog(level, f'weatherapi: {msg}')


def logdbg(msg):
    logmsg(logging.DEBUG if log else syslog.LOG_DEBUG, msg)


def loginf(msg):
    logmsg(logging.INFO if log else syslog.LOG_INFO, msg)


def logerr(msg):
    logmsg(logging.ERROR if log else syslog.LOG_ERR, msg)


class APIConfig:
    """Configuration for different API types"""
    
    OPENWEATHER = {
        'name': 'OpenWeatherMap',
        'polling_interval': 900,  # 15 minutes
        'url_template': (
            'https://api.openweathermap.org/data/3.0/onecall?'
            'lat={latitude}&lon={longitude}&exclude=current,minutely,hourly,daily'
            '&appid={app_id}&units=metric'
        ),
        'requires_app_id': True,
        'uses_coordinates': True
    }
    
    OPENMETEO = {
        'name': 'Open-Meteo Forecast',
        'polling_interval': 300,  # 5 minutes
        'url_template': (
            'https://api.open-meteo.com/v1/forecast?'
            'latitude={latitude}&longitude={longitude}'
            '&daily=weather_code,temperature_2m_max,temperature_2m_min,wind_speed_10m_max,'
            'wind_direction_10m_dominant,rain_sum,showers_sum,snowfall_sum,precipitation_sum,'
            'precipitation_hours,precipitation_probability_max'
            '&hourly=temperature_2m,precipitation,rain,showers,snowfall,snow_depth,weather_code,'
            'pressure_msl,surface_pressure,cloud_cover,visibility,evapotranspiration,'
            'vapour_pressure_deficit,wind_speed_10m,wind_direction_10m,wind_gusts_10m'
            '&current=temperature_2m,is_day,wind_speed_10m,wind_direction_10m,wind_gusts_10m,'
            'precipitation,rain,showers,snowfall,weather_code,cloud_cover,pressure_msl,surface_pressure'
        ),
        'requires_app_id': False,
        'uses_coordinates': True
    }
    
    AIRQUALITY = {
        'name': 'Open-Meteo Air Quality',
        'polling_interval': 900,  # 15 minutes
        'url_template': (
            'https://air-quality-api.open-meteo.com/v1/air-quality?'
            'latitude={latitude}&longitude={longitude}'
            '&current=european_aqi,us_aqi,pm10,pm2_5,carbon_monoxide,nitrogen_dioxide,'
            'sulphur_dioxide,ozone,aerosol_optical_depth,dust,ammonia,'
            'alder_pollen,birch_pollen,grass_pollen,mugwort_pollen,olive_pollen,'
            'ragweed_pollen'
        ),
        'requires_app_id': False,
        'uses_coordinates': True
    }
    
    METAR = {
        'name': 'CheckWX METAR',
        'polling_interval': 3600,  # 60 minutes
        'url_template': 'https://api.checkwx.com/metar/{metar_name}/decoded',
        'requires_app_id': True,
        'uses_coordinates': False,
        'uses_header_auth': True,
        'auth_header_name': 'X-API-Key'
    }
    
    EARTHQUAKES = {
        'name': 'EMSC Earthquakes',
        'polling_interval': 3600,  # 60 minutes
        'url_template': (
            'https://www.seismicportal.eu/fdsnws/event/1/query?'
            'limit=50&lat={latitude}&lon={longitude}&maxradius=180&minradius=10'
            '&format=json&minmag=2'
        ),
        'requires_app_id': False,
        'uses_coordinates': True
    }
    
    KI = {
        'name': 'NOAA K-Index Forecast',
        'polling_interval': 21600,  # 6 hours
        'url_template': 'https://services.swpc.noaa.gov/products/noaa-planetary-k-index-forecast.json',
        'requires_app_id': False,
        'uses_coordinates': False
    }
    
    K2 = {
        'name': 'NOAA Geospace DST',
        'polling_interval': 43200,  # 12 hours
        'url_template': 'https://services.swpc.noaa.gov/json/geospace/geospace_dst_7_day.json',
        'requires_app_id': False,
        'uses_coordinates': False
    }
    
    FLOOD = {
        'name': 'UK Environment Agency Flood',
        'polling_interval': 900,  # 15 minutes
        'url_template': (
            'https://environment.data.gov.uk/flood-monitoring/id/floods?'
            'lat={latitude}&long={longitude}&dist=10&min-severity=3'
        ),
        'requires_app_id': False,
        'uses_coordinates': True
    }
    
    AURORAWATCH = {
        'name': 'AuroraWatch UK',
        'polling_interval': 180,  # 3 minutes
        'url_template': 'http://aurorawatch-api.lancs.ac.uk/0.2.5/status/project/awn/sum-activity.xml',
        'requires_app_id': False,
        'uses_coordinates': False,
        'content_type': 'xml'
    }
    
    OVATION = {
        'name': 'NOAA OVATION Aurora',
        'polling_interval': 300,  # 5 minutes
        'url_template': 'https://services.swpc.noaa.gov/json/ovation_aurora_latest.json',
        'requires_app_id': False,
        'uses_coordinates': False
    }
    
    HEATALERT = {
        'name': 'UKHSA Heat Alert',
        'polling_interval': 1800,  # 30 minutes
        'url_template': 'https://ukhsa-dashboard.data.gov.uk/api/proxy/alerts/v1/heat/{location_code}',
        'requires_app_id': False,
        'uses_coordinates': False,
        'requires_location_code': True
    }
    
    COLDALERT = {
        'name': 'UKHSA Cold Alert',
        'polling_interval': 1800,  # 30 minutes
        'url_template': 'https://ukhsa-dashboard.data.gov.uk/api/proxy/alerts/v1/cold/{location_code}',
        'requires_app_id': False,
        'uses_coordinates': False,
        'requires_location_code': True
    }
    
    METOFFICERSS = {
        'name': 'Met Office Warnings RSS',
        'polling_interval': 300,  # 5 minutes
        'url_template': 'https://weather.metoffice.gov.uk/public/data/PWSCache/WarningsRSS/Region/{region_code}',
        'requires_app_id': False,
        'uses_coordinates': False,
        'requires_region_code': True,
        'content_type': 'xml'
    }
    
    XWEATHER = {
        'name': 'Xweather Forecast',
        'polling_interval': 300,  # 5 minutes
        'url_template': (
            'https://data.api.xweather.com/forecasts/{latitude},{longitude}?'
            'format=json&limit=32&lang=en&client_id={client_id}&client_secret={client_secret}'
        ),
        'requires_app_id': False,
        'uses_coordinates': True,
        'requires_client_credentials': True
    }

    BOLTEK = {
        'name': 'Boltek NGX Lightning Detector',
        'polling_interval': 5,  # 5 seconds - real-time lightning strike data
        'url_template': 'https://weathertest.mdmi.co.uk/sael/boltek/data/ngxdata.json',
        'requires_app_id': False,
        'uses_coordinates': False,
        'content_type': 'json'
    }

    CUSTOM = {
        'name': 'Custom API',
        'polling_interval': None,  # Will be set from config
        'url_template': None,  # Will be set from config
        'requires_app_id': False,
        'uses_coordinates': False,
        'is_custom': True
    }
    
    @classmethod
    def get_config(cls, api_type: str) -> Optional[Dict[str, Any]]:
        """Get configuration for a specific API type"""
        api_type_upper = api_type.upper()
        if hasattr(cls, api_type_upper):
            return getattr(cls, api_type_upper)
        return None


class WeatherAPIPoller:
    """Handles polling a single weather API source"""
    
    def __init__(self, name: str, config: Dict[str, Any], 
                 latitude: float, longitude: float):
        """
        Initialize the API poller
        
        Args:
            name: Name of the API source (e.g., 'Alerts', 'Forecast', 'Metar')
            config: Configuration dictionary for this API source
            latitude: Station latitude
            longitude: Station longitude
        """
        self.name = name
        self.latitude = latitude
        self.longitude = longitude
        
        # Check if this source is enabled
        self.enabled = config.get('enabled', True)  # Default to True for backward compatibility
        if not self.enabled:
            loginf(f"{self.name}: Disabled in configuration (enabled = False)")
            # Set minimal config for disabled sources
            self.api_config = {'name': 'Disabled'}
            self.data_path = None
            self.thread = None
            self.stop_event = None
            return
        
        # Get API type
        self.api_type = config.get('api_type', '').lower()
        if not self.api_type:
            raise ValueError(f"{self.name}: 'api_type' is required in configuration")
        
        # Get API configuration
        self.api_config = APIConfig.get_config(self.api_type)
        if not self.api_config:
            raise ValueError(f"{self.name}: Unknown api_type '{self.api_type}'")
        
        # Handle custom API configuration
        if self.api_config.get('is_custom', False):
            # Custom APIs require url and poll_interval
            custom_url = config.get('url', '')
            custom_interval = config.get('poll_interval', None)
            custom_headers = config.get('header', '')
            
            if not custom_url:
                raise ValueError(f"{self.name}: Custom API requires 'url' parameter")
            if custom_interval is None:
                raise ValueError(f"{self.name}: Custom API requires 'poll_interval' parameter (in seconds)")
            
            try:
                custom_interval = int(custom_interval)
                if custom_interval < 60:
                    logerr(f"{self.name}: poll_interval should be at least 60 seconds, got {custom_interval}")
            except (ValueError, TypeError):
                raise ValueError(f"{self.name}: poll_interval must be an integer (seconds)")
            
            # Parse custom headers if provided
            parsed_headers = {}
            if custom_headers:
                try:
                    # Split by pipe, then by colon
                    header_pairs = custom_headers.split('|')
                    for pair in header_pairs:
                        pair = pair.strip()
                        if ':' in pair:
                            key, value = pair.split(':', 1)
                            parsed_headers[key.strip()] = value.strip()
                        else:
                            logerr(f"{self.name}: Invalid header format (missing colon): '{pair}'")
                    
                    if parsed_headers:
                        loginf(f"{self.name}: Custom headers configured: {list(parsed_headers.keys())}")
                except Exception as e:
                    logerr(f"{self.name}: Failed to parse custom headers: {e}")
            
            # Override the config for custom API
            self.api_config = {
                'name': f'Custom API ({self.name})',
                'polling_interval': custom_interval,
                'url_template': custom_url,
                'requires_app_id': False,
                'uses_coordinates': False,
                'is_custom': True,
                'custom_headers': parsed_headers
            }
            
            loginf(f"{self.name}: Configured as custom API")
            loginf(f"{self.name}: URL: {custom_url[:100]}...")  # Truncate long URLs
            loginf(f"{self.name}: Polling interval: {custom_interval}s")
        
        # Get app_id if required (or X-API-Key for METAR)
        self.app_id = config.get('app_id', '') or config.get('X-API-Key', '')
        if self.api_config['requires_app_id']:
            if not self.app_id or self.app_id in ['dummy1234567', '<mymetarapikey>']:
                logerr(f"{self.name}: API type '{self.api_type}' requires a valid app_id or X-API-Key")
        
        # Get METAR-specific configuration
        self.metar_name = config.get('metar_name', '')
        if self.api_type == 'metar':
            if not self.metar_name or self.metar_name == '<mymetarname>':
                logerr(f"{self.name}: METAR api_type requires a valid metar_name (e.g., EGTK)")
        
        # Get location_code for heat/cold alerts
        self.location_code = config.get('location_code', '')
        if self.api_config.get('requires_location_code', False):
            if not self.location_code or self.location_code == '<code>':
                logerr(f"{self.name}: API type '{self.api_type}' requires a valid location_code (e.g., E12000008)")
        
        # Get region_code for Met Office RSS
        self.region_code = config.get('region_code', '')
        if self.api_config.get('requires_region_code', False):
            if not self.region_code or self.region_code == '<regioncode>':
                logerr(f"{self.name}: API type '{self.api_type}' requires a valid region_code (e.g., se)")
        
        # Get client credentials for Xweather
        self.client_id = config.get('client_id', '')
        self.client_secret = config.get('client_secret', '')
        if self.api_config.get('requires_client_credentials', False):
            if not self.client_id or self.client_id == '<client_id_here>':
                logerr(f"{self.name}: API type '{self.api_type}' requires a valid client_id")
            if not self.client_secret or self.client_secret == '<client_secret_here>':
                logerr(f"{self.name}: API type '{self.api_type}' requires a valid client_secret")
        
        # Get data path
        self.data_path = config.get('data_path', f'./jsondata/{name.lower()}.txt')
        
        # Get polling interval from API config
        self.polling_interval = self.api_config['polling_interval']
        
        self.last_poll_time = 0
        self.thread = None
        self.stop_event = threading.Event()
        
        loginf(f"{self.name}: Configured for {self.api_config['name']} "
               f"(polling every {self.polling_interval}s)")
        
        # Create directory for data file if it doesn't exist
        data_dir = os.path.dirname(self.data_path)
        if data_dir and not os.path.exists(data_dir):
            try:
                os.makedirs(data_dir, exist_ok=True)
                loginf(f"{self.name}: Created directory: {data_dir}")
            except Exception as e:
                logerr(f"{self.name}: Failed to create directory {data_dir}: {e}")
    
    def build_url(self) -> str:
        """Build the API URL with current parameters"""
        url_template = self.api_config['url_template']
        
        # For custom APIs, return URL as-is (user provides complete URL)
        if self.api_config.get('is_custom', False):
            return url_template
        
        # Build parameters dict based on what the API uses
        params = {}
        
        # Add coordinates if the API uses them
        if self.api_config.get('uses_coordinates', True):
            params['latitude'] = self.latitude
            params['longitude'] = self.longitude
        
        # Add app_id if required (for URL-based auth)
        if self.api_config['requires_app_id'] and not self.api_config.get('uses_header_auth', False):
            params['app_id'] = self.app_id
        
        # Add METAR-specific parameters
        if self.api_type == 'metar':
            params['metar_name'] = self.metar_name
        
        # Add location_code for heat/cold alerts
        if self.api_config.get('requires_location_code', False):
            params['location_code'] = self.location_code
        
        # Add region_code for Met Office RSS
        if self.api_config.get('requires_region_code', False):
            params['region_code'] = self.region_code
        
        # Add client credentials for Xweather
        if self.api_config.get('requires_client_credentials', False):
            params['client_id'] = self.client_id
            params['client_secret'] = self.client_secret
        
        # No extra params needed for fixed-URL APIs (Boltek, KI, K2, etc.)
        if params:
            return url_template.format(**params)
        return url_template
    
    def fetch_and_save(self) -> bool:
        """
        Fetch data from API and save to file
        
        Returns:
            bool: True if successful, False otherwise
        """
        url = self.build_url()
        
        try:
            logdbg(f"{self.name}: Fetching data from {self.api_config['name']}")
            
            # Build headers
            headers = {
                'User-Agent': 'WeeWX-WeatherAPI/2.0 (https://github.com/weewx/weewx)'
            }
            
            # Add custom headers for custom APIs
            if self.api_config.get('is_custom', False):
                custom_headers = self.api_config.get('custom_headers', {})
                if custom_headers:
                    headers.update(custom_headers)
                    logdbg(f"{self.name}: Added custom headers: {list(custom_headers.keys())}")
            
            # Add authentication header if API uses header-based authentication
            if self.api_config.get('uses_header_auth', False):
                auth_header_name = self.api_config.get('auth_header_name', 'X-API-Key')
                headers[auth_header_name] = self.app_id
                logdbg(f"{self.name}: Using header authentication ({auth_header_name})")
            
            # Make the request
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            
            # Save raw response to file
            with open(self.data_path, 'w') as f:
                f.write(response.text)
            
            loginf(f"{self.name}: Saved data to {self.data_path} "
                   f"({len(response.text)} bytes)")
            
            return True
            
        except requests.exceptions.RequestException as e:
            logerr(f"{self.name}: API request failed: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logerr(f"{self.name}: Response status: {e.response.status_code}")
                logerr(f"{self.name}: Response text: {e.response.text[:200]}")
            return False
        except IOError as e:
            logerr(f"{self.name}: Failed to write to {self.data_path}: {e}")
            return False
        except Exception as e:
            logerr(f"{self.name}: Unexpected error: {e}")
            return False
    
    def should_poll(self) -> bool:
        """Check if enough time has elapsed since last poll"""
        current_time = time.time()
        return (current_time - self.last_poll_time) >= self.polling_interval
    
    def poll_if_needed(self):
        """Poll the API if the interval has elapsed"""
        if self.should_poll():
            if self.fetch_and_save():
                self.last_poll_time = time.time()
    
    def start_background_polling(self):
        """Start background thread for periodic polling"""
        if not self.enabled:
            logdbg(f"{self.name}: Skipping background polling (disabled)")
            return
        
        if self.thread is None or not self.thread.is_alive():
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._polling_loop, daemon=True)
            self.thread.start()
            loginf(f"{self.name}: Started background polling thread")
    
    def stop_background_polling(self):
        """Stop the background polling thread"""
        if not self.enabled:
            return
        
        if self.thread and self.thread.is_alive():
            self.stop_event.set()
            self.thread.join(timeout=5)
            loginf(f"{self.name}: Stopped background polling thread")
    
    def _polling_loop(self):
        """Background thread polling loop"""
        # Do initial fetch immediately
        self.fetch_and_save()
        self.last_poll_time = time.time()
        
        while not self.stop_event.is_set():
            # Sleep in small intervals to allow responsive shutdown
            for _ in range(self.polling_interval):
                if self.stop_event.is_set():
                    break
                time.sleep(1)
            
            if not self.stop_event.is_set():
                self.fetch_and_save()
                self.last_poll_time = time.time()


class WeatherAPIService(StdService):
    """
    WeeWX service to poll weather APIs and save data to files
    """
    
    def __init__(self, engine, config_dict):
        """Initialize the service"""
        super(WeatherAPIService, self).__init__(engine, config_dict)
        
        loginf("Initializing WeatherAPI Service v2.0")
        
        # Get station location from WeeWX configuration
        try:
            self.latitude = float(config_dict['Station']['latitude'])
            self.longitude = float(config_dict['Station']['longitude'])
            loginf(f"Station location: {self.latitude}, {self.longitude}")
        except (KeyError, ValueError, TypeError) as e:
            logerr(f"Failed to get station location from config: {e}")
            self.latitude = 0.0
            self.longitude = 0.0
        
        # Get WeatherAPI configuration
        api_config = config_dict.get('WeatherAPI', {})
        
        if not api_config:
            logerr("No [WeatherAPI] section found in weewx.conf")
            self.pollers = []
            return
        
        # Create pollers for each configured API source
        self.pollers = []
        for source_name, source_config in api_config.items():
            if isinstance(source_config, dict):
                try:
                    poller = WeatherAPIPoller(
                        source_name, 
                        source_config,
                        self.latitude,
                        self.longitude
                    )
                    # Only add enabled pollers to the list
                    if poller.enabled:
                        self.pollers.append(poller)
                        loginf(f"Configured API source: {source_name} "
                               f"({poller.api_config['name']})")
                except Exception as e:
                    logerr(f"Failed to configure API source {source_name}: {e}")
        
        if not self.pollers:
            loginf("No API sources configured")
            return
        
        # Start background polling for all sources
        for poller in self.pollers:
            poller.start_background_polling()
        
        loginf(f"WeatherAPI Service initialized with {len(self.pollers)} source(s)")
    
    def shutDown(self):
        """Clean shutdown of the service"""
        loginf("Shutting down WeatherAPI Service")
        for poller in self.pollers:
            poller.stop_background_polling()


# For testing
if __name__ == '__main__':
    print("WeatherAPI Service for WeeWX 5.2")
    print("Version 2.0 - Multi-API Support")
    print("\nThis module should be loaded as a WeeWX service")
    print("\nSupported API types:")
    print("  - openweather: OpenWeatherMap (900s, requires app_id)")
    print("  - openmeteo: Open-Meteo Forecast (300s)")
    print("  - airquality: Open-Meteo Air Quality (900s)")
    print("  - metar: CheckWX METAR (3600s, requires X-API-Key + metar_name)")
    print("  - earthquakes: EMSC Earthquakes (3600s)")
    print("  - ki: NOAA K-Index Forecast (21600s)")
    print("  - k2: NOAA Geospace DST (43200s)")
    print("  - flood: UK Flood Warnings (900s)")
    print("  - aurorawatch: AuroraWatch UK (180s)")
    print("  - ovation: NOAA OVATION Aurora (300s)")
    print("  - heatalert: UKHSA Heat Alert (1800s, requires location_code)")
    print("  - coldalert: UKHSA Cold Alert (1800s, requires location_code)")
    print("  - metofficerss: Met Office Warnings RSS (300s, requires region_code)")
    print("  - xweather: Xweather Forecast (300s, requires client_id + client_secret)")
    print("  - boltek: Boltek NGX Lightning Detector (60s)")
    print("  - custom: User-defined API (requires url + poll_interval)")
    print("\nSee README.md for full documentation")
    print("\nExample configuration in weewx.conf:")
    print("""
[WeatherAPI]
    [[Forecast]]
        api_type = openmeteo
        data_path = /var/lib/weewx/jsondata/forecast.txt
    
    [[Flood]]
        api_type = flood
        data_path = /var/www/html/sael/jsondata/flood.txt
    
    [[HeatAlert]]
        api_type = heatalert
        location_code = E12000008
        data_path = /var/www/html/sael/jsondata/heat.txt
    
    [[MetOfficeRSS]]
        api_type = metofficerss
        region_code = se
        data_path = /var/www/html/sael/jsondata/metofficerss.txt
    
    [[Xweather]]
        api_type = xweather
        client_id = your_client_id
        client_secret = your_client_secret
        data_path = /var/www/html/sael/jsondata/xweather.txt
    
    [[Boltek]]
        api_type = boltek
        enabled = True
        data_path = /var/www/html/sael/jsondata/boltek.txt
    
[Engine]
    [[Services]]
        data_services = user.weatherapi.WeatherAPIService
""")

#!/usr/bin/env python3
#
# Generic Data Inject Service for WeeWX
# Reads data from multiple JSON files and injects into WeeWX loop packets and archive records
#
# Copyright (C) 2025 Ian Millard <weatherboyian@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import json
import logging
import os
import time
import threading

import weewx
from weewx.engine import StdService
from weeutil.weeutil import to_int, to_bool

log = logging.getLogger(__name__)

# Version history:
# 1.0.0 - Initial release with hardcoded air quality and weather sources
# 1.1.0 - Added SunSynk solar data support
# 1.2.0 - Made generic with configurable data sources and field mappings
# 1.3.0 - Fixed archive-only injection and nested configuration support
# 1.4.0 - Added comprehensive versioning and improved error handling
# 1.5.0 - Modified to inject into loop packets AND archive records

SERVICE_VERSION = "1.5.0"
SERVICE_NAME = "DataInjectService"

class DataInjectService(StdService):
    """
    Generic service to read data from JSON files and inject into WeeWX.
    
    Features:
    - Configurable data sources via weewx.conf
    - Flexible field mappings
    - JSON path navigation for nested data
    - Loop-time injection (for real-time data)
    - Archive-time injection (for historical data)
    - Disable individual data sources
    - Configurable update interval for loop data
    """
    
    def __init__(self, engine, config_dict):
        super(DataInjectService, self).__init__(engine, config_dict)
        
        # Log service version at startup
        log.info("%s version %s initializing", SERVICE_NAME, SERVICE_VERSION)
        
        # Get service configuration
        service_config = config_dict.get(SERVICE_NAME, {})
        
        # Configuration for loop packet injection
        self.loop_update_interval = to_int(service_config.get('loop_update_interval', 5))  # seconds
        self.loop_injection_enabled = to_bool(service_config.get('loop_injection_enabled', True))
        self.archive_injection_enabled = to_bool(service_config.get('archive_injection_enabled', True))
        
        # Cache for loop data to avoid reading files too frequently
        self.data_cache = {}
        self.cache_lock = threading.Lock()
        self.last_update_time = {}
        
        # Parse data sources from configuration
        self.data_sources = self.parse_data_sources(service_config)
        
        # Bind to both loop and archive events based on configuration
        if self.loop_injection_enabled:
            self.bind(weewx.NEW_LOOP_PACKET, self.handle_new_loop_packet)
            log.info("Loop packet injection enabled (interval: %d seconds)", self.loop_update_interval)
        
        if self.archive_injection_enabled:
            self.bind(weewx.NEW_ARCHIVE_RECORD, self.handle_new_archive_record)
            log.info("Archive record injection enabled")
        
        log.info("%s initialized successfully", SERVICE_NAME)
        log.info("Service version: %s", SERVICE_VERSION)
        log.info("Configured data sources: %s", list(self.data_sources.keys()))
        
        # Log each data source configuration
        for source_name, source_config in self.data_sources.items():
            status = "DISABLED" if source_config['disabled'] else f"{source_config['file_path']} ({len(source_config['field_mappings'])} fields)"
            log.info("  %s: %s", source_name, status)
        
        # Start a thread to periodically update loop data (if enabled)
        if self.loop_injection_enabled:
            self._start_loop_update_thread()
        
        log.info("%s ready - data injection configured for loop and archive", SERVICE_NAME)
    
    def _start_loop_update_thread(self):
        """Start a background thread to periodically update loop data."""
        def update_loop_data():
            while True:
                try:
                    # Update all enabled data sources
                    with self.cache_lock:
                        for source_name, source_config in self.data_sources.items():
                            if not source_config['disabled']:
                                current_time = time.time()
                                last_update = self.last_update_time.get(source_name, 0)
                                
                                # Only update if enough time has passed
                                if current_time - last_update >= self.loop_update_interval:
                                    data = self.read_data_file(source_config['file_path'], source_name)
                                    if data:
                                        self.data_cache[source_name] = {
                                            'data': data,
                                            'timestamp': current_time
                                        }
                                        self.last_update_time[source_name] = current_time
                                        log.debug("Updated loop data cache for %s", source_name)
                except Exception as e:
                    log.error("Error in loop update thread: %s", e)
                
                # Sleep for the update interval
                time.sleep(self.loop_update_interval)
        
        # Start the thread
        thread = threading.Thread(target=update_loop_data, daemon=True)
        thread.start()
        log.debug("Started loop data update thread")
    
    def parse_data_sources(self, service_config):
        """Parse data sources from weewx.conf configuration"""
        data_sources = {}
        sources_found = 0
        
        # Look for nested source sections
        for key, value in service_config.items():
            if key.startswith('source_') and isinstance(value, dict):
                source_name = key[7:]  # Remove 'source_' prefix
                data_sources[source_name] = self.parse_source_config(value, source_name)
                sources_found += 1
        
        log.debug("Parsed %d data source(s) from configuration", sources_found)
        return data_sources
    
    def parse_source_config(self, source_config, source_name):
        """Parse individual data source configuration from nested section"""
        config = {
            'file_path': '/path/to/default.json',
            'disabled': False,
            'field_mappings': {},
            'json_path': '',  # Optional JSON path to nested data
            'cache_ttl': to_int(source_config.get('cache_ttl', self.loop_update_interval))  # Cache TTL in seconds
        }
        
        # Get file path
        file_path = source_config.get('path', '/path/to/default.json')
        config['file_path'] = file_path
        config['disabled'] = file_path.lower() == 'disable'
        
        if config['disabled']:
            log.debug("Data source '%s' is disabled", source_name)
            return config
        
        # Get JSON path if specified
        config['json_path'] = source_config.get('json_path', '')
        if config['json_path']:
            log.debug("Data source '%s' using JSON path: %s", source_name, config['json_path'])
        
        # Parse field mappings
        mapping_config = source_config.get('mapping', {})
        if isinstance(mapping_config, dict):
            # Direct dictionary mapping
            config['field_mappings'] = mapping_config
            log.debug("Data source '%s' using dictionary mapping with %d fields", 
                     source_name, len(mapping_config))
        elif isinstance(mapping_config, str):
            # String format: "json_key1:db_col1, json_key2:db_col2"
            config['field_mappings'] = self.parse_field_mappings_string(mapping_config)
            log.debug("Data source '%s' using string mapping with %d fields", 
                     source_name, len(config['field_mappings']))
        elif isinstance(mapping_config, list):
            # List format for complex mappings
            config['field_mappings'] = self.parse_field_mappings_list(mapping_config)
            log.debug("Data source '%s' using list mapping with %d fields", 
                     source_name, len(config['field_mappings']))
        else:
            log.warning("Data source '%s' has invalid mapping configuration type: %s", 
                       source_name, type(mapping_config))
        
        return config
    
    def parse_field_mappings_string(self, field_mappings_str):
        """Parse field mappings from string format"""
        field_mappings = {}
        if field_mappings_str and isinstance(field_mappings_str, str):
            # Support multiple formats: comma, pipe, or space separated
            separators = [',', '|', ' ']
            separator = ','
            for sep in separators:
                if sep in field_mappings_str and ':' in field_mappings_str:
                    separator = sep
                    break
            
            mappings = field_mappings_str.split(separator)
            for mapping in mappings:
                mapping = mapping.strip()
                if ':' in mapping:
                    json_key, db_col = mapping.split(':', 1)
                    field_mappings[json_key.strip()] = db_col.strip()
                elif mapping:  # Non-empty but no colon
                    log.warning("Invalid mapping format (missing colon): '%s'", mapping)
        else:
            log.warning("Invalid field mappings string: %s", field_mappings_str)
        
        return field_mappings
    
    def parse_field_mappings_list(self, field_mappings_list):
        """Parse field mappings from list format"""
        field_mappings = {}
        if not isinstance(field_mappings_list, list):
            log.warning("Expected list for field mappings, got %s", type(field_mappings_list))
            return field_mappings
            
        for item in field_mappings_list:
            if isinstance(item, dict):
                # List of dictionaries: [{"json_key": "db_col"}, ...]
                field_mappings.update(item)
            elif isinstance(item, str) and ':' in item:
                # List of strings: ["json_key:db_col", ...]
                json_key, db_col = item.split(':', 1)
                field_mappings[json_key.strip()] = db_col.strip()
            elif isinstance(item, str) and item:
                log.warning("Invalid mapping format in list (missing colon): '%s'", item)
        
        return field_mappings
    
    def handle_new_loop_packet(self, event):
        """Handle new loop packet event - inject data into loop packets"""
        try:
            log.debug("Loop packet event received - %s version %s injecting external data", 
                     SERVICE_NAME, SERVICE_VERSION)
            
            with self.cache_lock:
                # Get current cached data for all sources
                for source_name, source_config in self.data_sources.items():
                    if not source_config['disabled']:
                        cache_entry = self.data_cache.get(source_name)
                        if cache_entry:
                            # Check if cache is still valid
                            current_time = time.time()
                            cache_age = current_time - cache_entry['timestamp']
                            if cache_age <= source_config.get('cache_ttl', self.loop_update_interval):
                                self.inject_data_into_packet(event.packet, cache_entry['data'], 
                                                            source_config, source_name, "loop")
                            else:
                                log.debug("Cache expired for %s (age: %.1fs)", 
                                         source_name, cache_age)
        
        except Exception as e:
            log.error("Failed to inject external data into loop packet: %s", e)
    
    def handle_new_archive_record(self, event):
        """Handle new archive record event - inject data into archive records"""
        try:
            log.debug("Archive record event received - %s version %s injecting external data", 
                     SERVICE_NAME, SERVICE_VERSION)
            
            # For archive, read fresh data from files (not from cache)
            for source_name, source_config in self.data_sources.items():
                if not source_config['disabled']:
                    data = self.read_data_file(source_config['file_path'], source_name)
                    if data:
                        self.inject_data_into_record(event.record, data, source_config, 
                                                    source_name, "archive")
            
            log.info("Archive data injection completed")
            
        except Exception as e:
            log.error("Failed to inject external data into archive record: %s", e)
    
    def inject_data_into_packet(self, packet, data, source_config, source_name, context="loop"):
        """Inject data into a loop packet"""
        return self._inject_data(packet, data, source_config, source_name, context)
    
    def inject_data_into_record(self, record, data, source_config, source_name, context="archive"):
        """Inject data into an archive record"""
        return self._inject_data(record, data, source_config, source_name, context)
    
    def _inject_data(self, target, data, source_config, source_name, context):
        """Generic method to inject data into either packet or record"""
        added_count = 0
        
        # Navigate to the appropriate level in JSON if json_path is specified
        target_data = data
        if source_config['json_path']:
            try:
                path_parts = source_config['json_path'].split('.')
                for part in path_parts:
                    if part:  # Skip empty parts
                        target_data = target_data[part]
                log.debug("Navigated to JSON path '%s' in %s data for %s", 
                         source_config['json_path'], source_name, context)
            except (KeyError, TypeError) as e:
                log.warning("JSON path '%s' not found in %s data for %s: %s", 
                           source_config['json_path'], source_name, context, e)
                return 0
        
        # Process each field mapping
        injected_fields = []
        for json_key, db_column in source_config['field_mappings'].items():
            if json_key in target_data:
                try:
                    value = target_data[json_key]
                    if value is not None:
                        target[db_column] = float(value)
                        added_count += 1
                        injected_fields.append(f"{db_column}={value}")
                    else:
                        log.debug("Skipping null value for %s.%s in %s", 
                                 source_name, json_key, context)
                except (TypeError, ValueError) as e:
                    log.warning("Could not convert value for %s.%s in %s: %s (value: %s)", 
                               source_name, json_key, context, e, target_data[json_key])
            else:
                log.debug("JSON key '%s' not found in %s data for %s", 
                         json_key, source_name, context)
        
        if added_count > 0:
            log.debug("Injected %d fields from %s into %s: %s", 
                     added_count, source_name, context, ", ".join(injected_fields))
        
        return added_count
    
    def read_data_file(self, file_path, source_name):
        """Read data from JSON file with improved error handling"""
        try:
            if not os.path.exists(file_path):
                log.error("Data file not found for %s: %s", source_name, file_path)
                return None
            
            file_stats = os.stat(file_path)
            file_size = file_stats.st_size
            file_mtime = file_stats.st_mtime
            file_age = time.time() - file_mtime
            
            # Warn if file is too old (more than 5 minutes)
            if file_age > 300:  # 5 minutes
                log.warning("Data file for %s is old (%.1f minutes): %s", 
                           source_name, file_age/60, file_path)
            
            with open(file_path, 'r') as f:
                data = json.load(f)
            
            log.debug("Successfully read data from %s: %s (%d bytes, modified %.1fs ago)", 
                     source_name, file_path, file_size, file_age)
            return data
            
        except json.JSONDecodeError as e:
            log.error("JSON decode error for %s file %s: %s", source_name, file_path, e)
        except PermissionError as e:
            log.error("Permission error reading %s file %s: %s", source_name, file_path, e)
        except Exception as e:
            log.error("Unexpected error reading %s file %s: %s", source_name, file_path, e)
        
        return None


# Version information for external access
def get_version():
    """Return the service version"""
    return SERVICE_VERSION

def get_service_info():
    """Return service information dictionary"""
    return {
        'name': SERVICE_NAME,
        'version': SERVICE_VERSION,
        'description': 'Generic data injection service for WeeWX (loop and archive)'
    }

#
#    Copyright (c) 2024 Sean Balfour <seanbalfourdresden@googlemail.com>
#
"""This example shows how to extend the XTypes system with a new type, AirDensity in kg/m³ 

REQUIRES WeeWX V4.2 OR LATER!

To use:
    1. Stop weewx
    2. Put the unitsExtra.py file in your user subdirectory. 
    3. Put the airdensity.py file in your user subdirectory.

    4. In weewx.conf, subsection [Engine][[Services]], 
    add AirDensityService to the list
    "xtype_services". For example, this means changing this

        [Engine]
            [[Services]]
                xtype_services = weewx.wxxtypes.StdWXXTypes, weewx.wxxtypes.StdPressureCooker, weewx.wxxtypes.StdRainRater

    to this:
        [Engine]
            [[Services]]
                xtype_services = weewx.wxxtypes.StdWXXTypes, weewx.wxxtypes.StdPressureCooker, weewx.wxxtypes.StdRainRater, user.airdensity.AirDensityService


    5. Add the following to your weewx.conf:

    [[[[Groups]]]]
                
        group_density = kg_per_meter_cubed   # No Option simply 'kg_per_meter_cubed'

     [[[[StringFormats]]]]
                
        kg_per_meter_cubed = %.5f           

#############################################

    [AirDensity]
        algorithm = simple  # in kg/m³

#############################################

    [StdWXCalculate]
    
        [[Calculations]]
        
            AirDensity = software


you can call the value in your tmpl like this:

// air density
$air_density["air_density"] = $current.AirDensity.format(add_label=False);

    6. Restart weewx

you can call the value in your tmpl like this:

// air density
$air_density["air_density"] = $current.AirDensity.format(add_label=False);

    6. Restart weewx


import math
import weewx
import weewx.units
import weewx.xtypes
from weewx.engine import StdService
from weewx.units import ValueTuple

"""

# Tell the unit system what group our new observation type, 'AirDensity', belongs to:
weewx.units.obs_group_dict['AirDensity'] = "group_density"
weewx.units.USUnits['group_density'] = 'kg_per_meter_cubed'
weewx.units.MetricUnits['group_density'] = 'kg_per_meter_cubed'
weewx.units.MetricWXUnits['group_density'] = 'kg_per_meter_cubed'
weewx.units.default_unit_format_dict['kg_per_meter_cubed'] = '%.5f'
weewx.units.conversionDict['kg_per_meter_cubed'] = {'kg_per_meter_cubed':  lambda x : x * 1.0}
weewx.units.default_unit_label_dict['kg_per_meter_cubed']  = ' kg/m³'

class AirDensity(weewx.xtypes.XType):

    def __init__(self, algorithm='simple'):
        # Save the algorithm to be used.
        self.algorithm = algorithm.lower()

    def get_scalar(self, obs_type, record, db_manager):
        # We only know how to calculate 'AirDensity'. For everything else, raise an exception UnknownType
        if obs_type != 'AirDensity':
            raise weewx.UnknownType(obs_type)

# pressure in hPa 
        if 'barometer' not in record or record['barometer'] is None:
            raise weewx.CannotCalculate(obs_type)
        unit_and_group = weewx.units.getStandardUnitType(record['usUnits'], 'barometer')
        outBarometer_vt = ValueTuple(record['barometer'], *unit_and_group)
        outBarometer_hPa_vt = weewx.units.convert(outBarometer_vt, 'hPa')
        outBarometer_hPa = outBarometer_hPa_vt[0]

# out humidity in %
        if 'outHumidity' not in record or record['outHumidity'] is None:
            raise weewx.CannotCalculate(obs_type)
        unit_and_group = weewx.units.getStandardUnitType(record['usUnits'], 'outHumidity')
        outHumidity_vt = ValueTuple(record['outHumidity'], *unit_and_group)
        outHumidity = outHumidity_vt[0]        

# out temp in °C
        if 'outTemp' not in record or record['outTemp'] is None:
            raise weewx.CannotCalculate(obs_type)        
        unit_and_group = weewx.units.getStandardUnitType(record['usUnits'], 'outTemp')
        outTemp_vt = ValueTuple(record['outTemp'], *unit_and_group)
        outTemp_C_vt = weewx.units.convert(outTemp_vt, 'degree_C')
        outTemp_C = outTemp_C_vt[0]

        if self.algorithm == 'simple':
        # "Simple" algorithm.

            # formula to get vapor pressure in °C
            vpRH = (outHumidity / 100.0) * 6.112 * math.exp(17.67 * outTemp_C / (outTemp_C + 243.5))

            # formula to get air density in kg/m³           
            T = outTemp_C
            P = outBarometer_hPa
            Es = vpRH # vapor pressure in °C
            Rv = 461.4964 # gas constant
            Rd = 287.0531 # gas constant
            tk = T + 273.15
            pv = Es * 100.0
            pd = (P - Es) * 100.0
            Density = (pv / (Rv * tk)) + (pd / (Rd * tk))
            Air_Density = ValueTuple(Density, 'kg_per_meter_cubed', 'group_density')

        return Air_Density

class AirDensityService(StdService):
    """ WeeWX service whose job is to register the XTypes extension AirDensity with the
    XType system.
    """
    def __init__(self, engine, config_dict):
        super(AirDensityService, self).__init__(engine, config_dict)

        # Get the desired algorithm. Default to "simple".
        try:
            algorithm = config_dict['AirDensity']['algorithm']
        except KeyError:
            algorithm = 'simple'

        # Instantiate an instance of AirDensity:
        self.ad = AirDensity(algorithm)
        # Register it:
        weewx.xtypes.xtypes.append(self.ad)

    def shutDown(self):
        # Remove the registered instance:
        weewx.xtypes.xtypes.remove(self.ad)

""" sunshine duration """

import syslog
from math import sin, cos, pi, asin
from datetime import datetime
import time
import weewx
from weewx.wxengine import StdService


weewx.units.obs_group_dict['sunshine_time'] = 'group_interval'

class SunshineDuration(StdService):
    def __init__(self, engine, config_dict):
        # Pass the initialization information on to my superclass:
        super(SunshineDuration, self).__init__(engine, config_dict)

        # Start intercepting events:
        self.bind(weewx.NEW_LOOP_PACKET, self.newLoopPacket)
        self.bind(weewx.NEW_ARCHIVE_RECORD, self.newArchiveRecord)
        self.lastdateTime = 0
        self.LoopDuration = 0
        self.sunshineSeconds = 0
        self.lastThreshold = 0
        self.firstArchive = True
        self.cum_time=0

    def newLoopPacket(self, event):
        """Gets called on a new loop packet event."""
        radiation = event.packet.get('radiation')
        if radiation is not None:
            if self.lastdateTime == 0:
                self.lastdateTime = event.packet.get('dateTime')
            self.LoopDuration = event.packet.get('dateTime') - self.lastdateTime
            self.lastdateTime = event.packet.get('dateTime')
            threshold = self.sunshineThreshold(event.packet.get('dateTime'))
            if radiation > threshold and threshold > 0:
                self.sunshineSeconds += self.LoopDuration
            self.cum_time += self.LoopDuration
            self.lastThreshold = threshold
            logdbg("Calculated LOOP sunshine_time = %f, based on radiation = %f, and threshold = %f" % (
                self.LoopDuration, radiation, threshold))

    def newArchiveRecord(self, event):
        """Gets called on a new archive record event."""
        radiation = event.record.get('radiation')
        threshold = self.sunshineThreshold(event.record.get('dateTime'))
        
        if self.lastdateTime == 0 or self.firstArchive:  # LOOP packets not yet captured : missing archive record extracted from datalogger at start OR first archive record after weewx start
            event.record['sunshine_time'] = 0.0
            event.record['sunshine_time_hours'] = 0.0
            event.record['threshold'] = self.lastThreshold
            if radiation is not None:
                self.lastThreshold = threshold
                if radiation > threshold and threshold > 0:
                    event.record['sunshine_time'] = event.record['interval']
                    event.record['sunshine_time_hours'] = event.record['interval'] / 60
                    event.record['is_sunshine']=1
                    event.record['threshold'] = self.lastThreshold
                else:
                     event.record['is_sunshine']=0
                     event.record['threshold'] = self.lastThreshold
                if self.lastdateTime != 0:  # LOOP already started, this is the first regular archive after weewx start
                    self.firstArchive = False
                loginf("Estimated sunshine duration from archive record= %f min, radiation = %f, and threshold = %f" % (
                    event.record['sunshine_time'], event.record['radiation'], self.lastThreshold))
        else:
            if radiation is not None:
                if radiation > threshold and threshold > 0:
                    event.record['is_sunshine']=1
                    event.record['threshold'] = self.lastThreshold
                else:
                    event.record['is_sunshine']=0
                    event.record['threshold'] = self.lastThreshold
            if self.cum_time > 0:  # do not divide by zero!
                event.record['sunshine_time'] = self.sunshineSeconds/self.cum_time * event.record['interval']
                event.record['sunshine_time_hours'] = self.sunshineSeconds/self.cum_time * event.record['interval'] / 60
            else: 
                 event.record['sunshine_time'] = 0
                 event.record['sunshine_time_hours'] = 0
            loginf("Sunshine duration from loop packets = %f min" % (event.record['sunshine_time']))

        self.sunshineSeconds = 0
        self.cum_time = 0

    def sunshineThreshold(self, mydatetime):
        utcdate = datetime.datetime.utcfromtimestamp(mydatetime)
        dayofyear = int(time.strftime("%j", time.gmtime(mydatetime)))
        theta = 360 * dayofyear / 365
        equatorialtime = 0.0172 + 0.4281 * cos((pi / 180) * theta) - 7.3515 * sin(
            (pi / 180) * theta) - 3.3495 * cos(2 * (pi / 180) * theta) - 9.3619 * sin(
            2 * (pi / 180) * theta)

        latitude = float(self.config_dict["Station"]["latitude"])
        longitude = float(self.config_dict["Station"]["longitude"])
        correctedtime = longitude * 4
        declination = asin(0.006918 - 0.399912 * cos((pi / 180) * theta) + 0.070257 * sin(
            (pi / 180) * theta) - 0.006758 * cos(2 * (pi / 180) * theta) + 0.000908 * sin(
            2 * (pi / 180) * theta)) * (180 / pi)
        minutesday = utcdate.hour * 60 + utcdate.minute
        solartime = (minutesday + correctedtime + equatorialtime) / 60
        hourly_angle = (solartime - 12) * 15
        sun_height = asin(sin((pi / 180) * latitude) * sin((pi / 180) * declination) + cos(
            (pi / 180) * latitude) * cos((pi / 180) * declination) * cos((pi / 180) * hourly_angle)) * (180 / pi)
        if sun_height > 3:
            threshold = (0.73 + 0.06 * cos((pi / 180) * 360 * dayofyear / 365)) * 1080 * pow(
                (sin(pi / 180 * sun_height)), 1.25) 
        else :
            threshold=0
        return threshold

#
#    Copyright (c) 2025 Sean Balfour <seanbalfourdresden@googlemail.com>
#
"""This example shows how to extend the XTypes system with a new type, Vapour Pressure Deficit (vpd) in kPa 

REQUIRES WeeWX V5 OR LATER!

To use:
    1. Stop weewx 
    2. Put the vpd.py file in your user subdirectory.
    3. In weewx.conf, subsection [Engine][[Services]], 
    add vpdService to the list
    "xtype_services". For example, this means changing this

        [Engine]
            [[Services]]
                xtype_services = weewx.wxxtypes.StdWXXTypes, weewx.wxxtypes.StdPressureCooker, weewx.wxxtypes.StdRainRater

    to this:
        [Engine]
            [[Services]]
                xtype_services = weewx.wxxtypes.StdWXXTypes, weewx.wxxtypes.StdPressureCooker, weewx.wxxtypes.StdRainRater, user.vpd.vpdService


    4. Add the following stanza to your weewx.conf:

#############################################

    [vpd]
        algorithm = tetens  # in kPa

#############################################

    5. Restart weewx
"""

""" Vapour Pressure Deficit """

# Tell the unit system what group our new observation type, 'vpd', belongs to:
weewx.units.obs_group_dict['vpd'] = "group_pressure"

class vpd(weewx.xtypes.XType):

    def __init__(self, algorithm='simple'):
        # Save the algorithm to be used.
        self.algorithm = algorithm.lower()

    def get_scalar(self, obs_type, record, db_manager):
        # We only know how to calculate 'vpd. For everything else, raise an exception UnknownType
        if obs_type != 'vpd':
            raise weewx.UnknownType(obs_type)

        # out humidity in %
        if 'outHumidity' not in record or record['outHumidity'] is None:
            raise weewx.CannotCalculate(obs_type)
        unit_and_group = weewx.units.getStandardUnitType(record['usUnits'], 'outHumidity')
        outHumidity_vt = ValueTuple(record['outHumidity'], *unit_and_group)
        outHumidity = outHumidity_vt[0]        

        # out temp in °C
        if 'outTemp' not in record or record['outTemp'] is None:
            raise weewx.CannotCalculate(obs_type)        
        unit_and_group = weewx.units.getStandardUnitType(record['usUnits'], 'outTemp')
        outTemp_vt = ValueTuple(record['outTemp'], *unit_and_group)
        outTemp_C_vt = weewx.units.convert(outTemp_vt, 'degree_C')
        outTemp_C = outTemp_C_vt[0]

        if self.algorithm == 'simple':
            # "simple" algorithm.

            T = outTemp_C
            H = outHumidity

            # vapour pressure of leaf
            a = (17.27 * T) / (T + 237.3)
            vpl = 0.61078 * math.exp(a)

            # vapour pressure of air
            b = (17.27 * T) / (T + 237.3) 
            vpa = 0.61078 * math.exp(b) * (H / 100.0)

            vpd_inHg = vpl - vpa

            # Form a ValueTuple
            vpd = ValueTuple(vpd_inHg, 'inHg', 'group_pressure')

        elif self.algorithm == 'tetens':
            # Use teten's algorithm.

            T = outTemp_C
            H = outHumidity

            # Use the formula. Results will be in kPa:

            # vapour pressure of leaf
            a = (17.27 * T) / (T + 237.3)
            vpl = 0.61078 * math.exp(a)

            # vapour pressure of air
            b = (17.27 * T) / (T + 237.3) 
            vpa = 0.61078 * math.exp(b) * (H / 100.0)

            vpd_kPa = vpl - vpa

            # Form a ValueTuple
            vpd = ValueTuple(vpd_kPa, 'kPa', 'group_pressure')
        else:
            # Don't recognize the exception. Fail hard:
            raise ValueError(self.algorithm)

        return vpd

class vpdService(StdService):

    def __init__(self, engine, config_dict):
        super(vpdService, self).__init__(engine, config_dict)

        # Get the desired algorithm. Default to "simple".
        try:
            algorithm = config_dict['vpd']['algorithm']
        except KeyError:
            algorithm = 'simple'

        # Instantiate an instance of Vapor Pressure Deficit:
        self.vpdx = vpd(algorithm)
        # Register it:
        weewx.xtypes.xtypes.append(self.vpdx)

    def shutDown(self):
        # Remove the registered instance:
        weewx.xtypes.xtypes.remove(self.vpdx)

#
#    Copyright (c) 2020 Tom Keffer <tkeffer@gmail.com>
#
#    See the file LICENSE.txt for your full rights.
#
"""This example shows how to extend the XTypes system with a new type, lastnonzero, the last non-null or non-zero in a record

REQUIRES WeeWX V4.2 OR LATER!

To use:
    1. Stop weewxd
    2. Put this file in your user subdirectory.
    3. In weewx.conf, subsection [Engine][[Services]], add LastNonZero to the list
    "xtype_services". For example, this means changing this

        [Engine]
            [[Services]]
                xtype_services = weewx.wxxtypes.StdWXXTypes, weewx.wxxtypes.StdPressureCooker, weewx.wxxtypes.StdRainRater

    to this:

        [Engine]
            [[Services]]
                xtype_services = weewx.wxxtypes.StdWXXTypes, weewx.wxxtypes.StdPressureCooker, weewx.wxxtypes.StdRainRater, user.lastnonzero.LastNonZeroService

    4. Optionally, add the following section to weewx.conf:
        [LastNonZero]
            algorithm = simple   # Or tetens

    5. Restart weewxd

"""
from weewx.engine import StdService
import weedb
import weewx.xtypes
import datetime

class LastNonZero(weewx.xtypes.XType):
   
    def get_aggregate(self, obs_type, timespan, aggregate_type, db_manager, **option_dict):
        if aggregate_type != 'lastnonzero':
            raise weewx.UnknownAggregation(aggregate_type)
       
        interpolate_dict = {
            'aggregate_type': aggregate_type,
            'obs_type': obs_type,
            'table_name': db_manager.table_name,
            'start': timespan.start,
            'stop': timespan.stop
        }

        select_stmt = "SELECT %(obs_type)s FROM %(table_name)s " \
                      "WHERE dateTime > %(start)s AND dateTime <= %(stop)s " \
                      "AND %(obs_type)s IS NOT NULL " \
                      "AND %(obs_type)s != 0 " \
                      "ORDER BY dateTime DESC LIMIT 1" % interpolate_dict

        try:
            row = db_manager.getSql(select_stmt)
        except weedb.NoColumnError:
            raise weewx.UnknownType(obs_type)

        value = row[0] if row else None

        u, g = weewx.units.getStandardUnitType(db_manager.std_unit_system, obs_type,
                                               aggregate_type)
        return weewx.units.ValueTuple(value, u, g)

class LastNonZeroService(StdService):
    """ WeeWX service whose job is to register the XTypes extension LastNonZero with the
    XType system.
    """

    def __init__(self, engine, config_dict):
        super(LastNonZeroService, self).__init__(engine, config_dict)

        # Instantiate an instance of LastNonZero:
        self.nz = LastNonZero()
        # Register it:
        weewx.xtypes.xtypes.append(self.nz)

    def shutDown(self):
        # Remove the registered instance:
        weewx.xtypes.xtypes.remove(self.nz)        



#    Copyright (c) 2022 Tom Keffer <tkeffer@gmail.com>
#    See the file LICENSE.txt for your rights.

"""Pick a color on the basis of a value. This version uses information from
the skin configuration file.

*******************************************************************************

This search list extension offers an extra tag:

    'colorize': Returns a color depending on a value

*******************************************************************************

To use this search list extension:

1) Copy this file to the user directory.

    For example, for pip installs:

        cp colorize_3_1.py ~/weewx-data/bin/user

    For package installers:

        sudo cp colorize_3_1.py /usr/share/weewx/user

2) Modify the option search_list_extensions in the skin.conf configuration file, adding
the name of this extension.  When you're done, it will look something like this:

    [CheetahGenerator]
        search_list_extensions = user.colorize_3_1.Colorize

3) Add a section [Colorize] to skin.conf. For example, this version would
allow you to colorize both temperature and UV values:

    [Colorize]
        [[group_temperature]]
            unit_system = metricwx
            default = tomato
            None = lightgray
            [[[upper_bounds]]]
                -10 = magenta
                0 = violet
                10 = lavender
                20 = moccasin
                30 = yellow
                40 = coral
        [[group_uv]]
            unit_system = metricwx
            default = darkviolet
            [[[upper_bounds]]]
                2.4 = limegreen
                5.4 = yellow
                7.4 = orange
                10.4 = red

You can then colorize backgrounds. For example, to colorize an HTML table cell:

<table>
  <tr>
    <td>Outside temperature</td>
    <td style="background-color:$colorize($current.outTemp)">$current.outTemp</td>
  </tr>
</table>

*******************************************************************************
"""

import weewx.units
from weewx.cheetahgenerator import SearchList

class Colorize(SearchList):                                               # 1

    def __init__(self, generator):                                        # 2
        SearchList.__init__(self, generator)
        self.color_tables = self.generator.skin_dict.get('Colorize', {})

    def colorize(self, value_vh):
        """
        Pick a color on the basis of a value. The color table will be obtained
        from the configuration file.

        Args:
            value_vh (ValueHelper): The value, represented as ValueHelper.

        Returns:
            str: A color string.
        """
        
        # Handle None or invalid input
        if value_vh is None:
            return "#cccccc"  # Default gray for None input
            
        # Get the ValueTuple from the incoming ValueHelper
        try:
            value_vt = value_vh.value_t
        except AttributeError:
            # If value_vh doesn't have value_t attribute
            return "#cccccc"
        
        # Handle None value_vt
        if value_vt is None:
            return "#cccccc"
        
        # Check if this is an UnknownObsType (doesn't have group attribute)
        # The safest approach is to check for the presence of group attribute
        if not hasattr(value_vt, 'group'):
            # This is likely an UnknownObsType or similar
            return "#cccccc"
            
        # Now it's safe to access group
        unit_group = value_vt.group

        # Make sure unit_group is in the color table, and that the table
        # specifies a unit system.
        if unit_group not in self.color_tables \
                or 'unit_system' not in self.color_tables[unit_group]:    # 5
            return "#cccccc"

        # Convert the value to the same unit used by the color table:
        unit_system = self.color_tables[unit_group]['unit_system']        # 6
        try:
            converted_vt = weewx.units.convertStdName(value_vt, unit_system)  # 7
        except (AttributeError, TypeError, ValueError, KeyError):
            # If conversion fails, return default color
            return self.color_tables[unit_group].get('default', "#cccccc")

        # Check for a value of None
        if converted_vt.value is None:                                    # 8
            return self.color_tables[unit_group].get('none') \
                   or self.color_tables[unit_group].get('None', "#cccccc")

        # Search for the value in the color table:
        try:
            for upper_bound in self.color_tables[unit_group]['upper_bounds']: # 9
                try:
                    if converted_vt.value <= float(upper_bound):                  # 10
                        return self.color_tables[unit_group]['upper_bounds'][upper_bound]
                except (ValueError, TypeError):
                    # Skip invalid bounds
                    continue
        except KeyError:
            # No upper_bounds defined
            pass

        return self.color_tables[unit_group].get('default', "#cccccc")   # 11

#
#    Copyright (c) 2026 Tom Keffer <tkeffer@gmail.com>
#
#    See the file LICENSE.txt for your full rights.
#

"""Search list extension to calculate when an SQL statement last evaluted true, or how long since it evaluated true.

Example:

    <p>It last rained at $time_at('rain>0') ($time_since('rain>0') ago).</p>

would result in

    <p>It last rained 20 June 2020 (81 days, 1 hour, 35 minutes ago).</p>

"""
from weewx.cheetahgenerator import SearchList

from weewx.units import ValueTuple, ValueHelper

VERSION = "0.4"


class TimeSince(SearchList):
    def get_extension_list(self, timespan, db_lookup):
        def time_since(expression, data_binding=None):
            """Time since a sql expression evaluted true"""
            db_manager = db_lookup(data_binding=data_binding)
            sql_stmt = "SELECT dateTime FROM %s WHERE %s AND dateTime <= %d ORDER BY dateTime DESC LIMIT 1" \
                       % (db_manager.table_name, expression, timespan.stop)

            row = db_manager.getSql(sql_stmt)
            val = timespan.stop - row[0] if row else None
            vt = ValueTuple(val, 'second', 'group_deltatime')
            vh = ValueHelper(vt,
                             context='month',
                             formatter=self.generator.formatter,
                             converter=self.generator.converter)
            return vh

        def time_at(expression, data_binding=None):
            """When an sql expression evaluated true"""
            db_manager = db_lookup(data_binding=data_binding)
            sql_stmt = "SELECT dateTime FROM %s WHERE %s AND dateTime <= %d ORDER BY dateTime DESC LIMIT 1" \
                       % (db_manager.table_name, expression, timespan.stop)

            row = db_manager.getSql(sql_stmt)
            val = row[0] if row else None
            vt = ValueTuple(val, 'unix_epoch', 'group_time')
            vh = ValueHelper(vt,
                             formatter=self.generator.formatter,
                             converter=self.generator.converter)
            return vh

        return [{
            'time_since': time_since,
            'time_at': time_at,
        }]

# sael_extras.py
# Captures selected loop packet fields and stores them in sael_extras.sdb
# Data is written once per archive interval, synchronised with weewx.sdb

import syslog

import weewx
import weewx.manager
from weewx.engine import StdService

# Must match sael_extras.sdb exactly
schema = [('dateTime',          'INTEGER NOT NULL UNIQUE PRIMARY KEY'),
        ('usUnits',             'INTEGER NOT NULL'),
        ('interval',            'INTEGER NOT NULL'),
        ('aerosol_optical_depth',           'REAL'),
        ('AirDensity',                      'REAL'),
        ('cloudcover',                      'REAL'),
        ('dust',                            'REAL'),
        ('lightning_last_det_time',         'INTEGER'),
        ('alder_pollen',                    'REAL'),               
        ('birch_pollen',                    'REAL'),
        ('olive_pollen',                    'REAL'),
        ('grass_pollen',                    'REAL'),
        ('mugwort_pollen',                  'REAL'),
        ('ragweed_pollen',                  'REAL'),
        ('p_rain',                          'REAL'),
        ('p_rainRate',                      'REAL'),
        ('p_hourRain',                      'REAL'),
        ('p_dayRain',                       'REAL'),
        ('p_weekRain',                      'REAL'),
        ('p_monthRain',                     'REAL'),
        ('p_yearRain',                      'REAL'),
        ('p_stormRain',                     'REAL'),
        ('isRaining',                       'REAL'),
        ('hourRain',                        'REAL'),
        ('dayRain',                         'REAL'),
        ('weekRain',                        'REAL'),
        ('monthRain',                       'REAL'),
        ('yearRain',                        'REAL'),
        ('stormRain',                       'REAL'),
        ('sunshine_time',                   'REAL'),
        ('sunshine_time_hours',             'REAL'),
        ('is_sunshine',                     'REAL'),
        ('threshold',                       'REAL'),
        ('vpd',                             'REAL'),        
        ('pm4_0',                           'REAL'),
        ('pm2_5_SDS',                       'REAL'),
        ('pm10_0_SDS',                      'REAL'),
        ('pv_power',                        'REAL'),
        ('pv_voltage_1',                    'REAL'),
        ('pv_voltage_2',                    'REAL'),
        ('pv_current_1',                    'REAL'),
        ('pv_current_2',                    'REAL'),
        ('pv_power_1',                      'REAL'),
        ('pv_power_2',                      'REAL'),
        ('pv_energy_today',                 'REAL'),
        ('battery_power',                   'REAL'),
        ('battery_voltage',                 'REAL'),
        ('battery_current',                 'REAL'),
        ('battery_soc',                     'REAL'),
        ('battery_temp',                    'REAL'),
        ('battery_charge_today',            'REAL'),
        ('battery_discharge_today',         'REAL'),
        ('battery_discharge_total',         'REAL'),
        ('grid_power',                      'REAL'),
        ('grid_power_ct',                   'REAL'),
        ('grid_voltage',                    'REAL'),
        ('grid_frequency',                  'REAL'),
        ('grid_import_today',               'REAL'),
        ('grid_import_total',               'REAL'),
        ('grid_export_today',               'REAL'),
        ('load_power',                      'REAL'),
        ('load_power_essential',            'REAL'),
        ('load_power_non_essential',        'REAL'),
        ('load_percentage',                 'REAL'),
        ('inverter_temp',                   'REAL'),
        ('ac_output_frequency',             'REAL'),
        ('ac_output_voltage',               'REAL')]

# Column names derived from schema for filtering loop packets
SCHEMA_COLS = set(col for col, _ in schema)


class SaelExtrasService(StdService):

    def __init__(self, engine, config_dict):
        super().__init__(engine, config_dict)

        self.dbm = self.engine.db_binder.get_manager(
            data_binding='sael_extras_binding',
            initialize=True
        )

        # Verify schema matches the live database
        db_cols  = self.dbm.connection.columnsOf(self.dbm.table_name)
        mem_cols = [col for col, _ in schema]
        if db_cols != mem_cols:
            raise Exception(
                'sael_extras schema mismatch:\n  db : %s\n  code: %s'
                % (db_cols, mem_cols)
            )

        # Read the unit system from [StdConvert] target_unit — the same value
        # used by the main weewx database, guaranteeing both databases match.
        unit_system_str = config_dict['StdConvert']['target_unit']
        self.unit_system = weewx.units.unit_constants[unit_system_str]
        syslog.syslog(syslog.LOG_INFO,
                      'SaelExtrasService: using unit system %s (0x%02x) from [StdConvert] target_unit'
                      % (unit_system_str, self.unit_system))

        self.latest_loop_packet = None

        self.bind(weewx.NEW_LOOP_PACKET, self.new_loop_packet)
        self.bind(weewx.NEW_ARCHIVE_RECORD, self.new_archive_record)

    def new_loop_packet(self, event):
        # Just cache the latest loop packet; don't write anything yet
        self.latest_loop_packet = event.packet

    def new_archive_record(self, event):
        if self.latest_loop_packet is None:
            syslog.syslog(syslog.LOG_WARNING,
                          'SaelExtrasService: no loop packet cached, skipping record for dateTime %d'
                          % event.record['dateTime'])
            return

        # Merge loop packet and archive record.
        # Loop packet provides high-frequency sensor data.
        # Archive record provides calculated fields (AirDensity, sunshine_time,
        # vpd, windchill, dewpoint, etc.) added by process_services and
        # other NEW_ARCHIVE_RECORD listeners such as SunshineDuration.
        # Archive record values take priority over loop packet values.
        merged = {}
        merged.update(self.latest_loop_packet)
        merged.update(event.record)

        record = {
            'dateTime': event.record['dateTime'],
            'usUnits':  self.unit_system,
            'interval': event.record['interval'],
        }

        for col in SCHEMA_COLS - {'dateTime', 'usUnits', 'interval'}:
            if col in merged:
                record[col] = merged[col]

        if len(record) > 3:
            try:
                self.dbm.addRecord(record)
            except Exception as e:
                syslog.syslog(syslog.LOG_ERR,
                              'SaelExtrasService: failed to add record for dateTime %d: %s'
                              % (event.record['dateTime'], e))
        else:
            syslog.syslog(syslog.LOG_WARNING,
                          'SaelExtrasService: no extras fields found, skipping record for dateTime %d'
                          % event.record['dateTime'])

    def shutDown(self):
        try:
            self.dbm.close()
        except:
            pass
