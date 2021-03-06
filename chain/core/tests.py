from django.test import TestCase
from datetime import datetime, timedelta
import random
import json
import zmq
from django.utils.timezone import make_aware, utc, now
from pytz import AmbiguousTimeError
import re
import time

fake_zmq_socket = None

class FakeZMQContext(object):

    def socket(self, socket_type):
        global fake_zmq_socket
        fake_zmq_socket = FakeZMQSocket(socket_type)
        return fake_zmq_socket


class FakeZMQSocket(object):

    def __init__(self, socket_type):
        self._type = socket_type
        self.sent_msgs = {}

    def bind(self, *args, **kwargs):
        pass

    def connect(self, *args, **kwargs):
        pass

    def send(self, msg):
        topic, _, msg = msg.partition(' ')
        if topic not in self.sent_msgs:
            self.sent_msgs[topic] = []
        self.sent_msgs[topic].append(json.loads(msg))

    def send_string(self, msg):
        self.send(msg)

    def clear(self):
        self.sent_msgs = {}

# monkey patch so we can check what messages the API is sending
# anything that imports chain.api should come after this
zmq.Context = FakeZMQContext

from chain.core.models import Unit, Metric, Device, ScalarSensor, Site, \
    PresenceSensor, Person, Metadata
from chain.core.models import GeoLocation
from chain.core.resources import DeviceResource
from chain.core.api import HTTP_STATUS_SUCCESS, HTTP_STATUS_CREATED
from chain.core.hal import HALDoc
from chain.core import resources
from chain.localsettings import INFLUX_HOST, INFLUX_PORT, INFLUX_MEASUREMENT
from chain.influx_client import InfluxClient

resources.influx_client = InfluxClient(INFLUX_HOST, INFLUX_PORT, 'test',
                                       INFLUX_MEASUREMENT)

HTTP_STATUS_NOT_ACCEPTABLE = 406
HTTP_STATUS_NOT_FOUND = 404
HTTP_STATUS_BAD_REQUEST = 400

BASE_API_URL = '/'
SCALAR_DATA_URL = BASE_API_URL + 'scalar_data/'
SITES_URL = BASE_API_URL + 'sites/'


ACCEPT_TAIL = 'application/xhtml+xml,application/xml;q=0.9,\
        image/webp,*/*;q=0.8'


def obj_from_filled_schema(schema):
    '''Creates an object corresponding to the default values provided with
    a form schema'''
    obj = {}
    for k, v in schema['properties'].iteritems():
        if v['type'] == 'object':
            subobj = obj_from_filled_schema(v)
            if subobj:
                obj[k] = subobj
        elif 'default' in v:
            obj[k] = v['default']
    return obj


class HalTests(TestCase):

    def setUp(self):
        self.test_doc = {
            '_links': {'self': {'href': 'http://example.com'}},
            'attr1': 1,
            'attr2': 2
        }

    def test_basic_attrs_are_available(self):
        haldoc = HALDoc(self.test_doc)
        self.assertEqual(haldoc.attr1, self.test_doc['attr1'])
        self.assertEqual(haldoc.attr2, self.test_doc['attr2'])

    def test_links_is_an_attr_without_underscore(self):
        haldoc = HALDoc(self.test_doc)
        self.assertIn('self', haldoc.links)

    def test_link_href_is_available_as_attr(self):
        haldoc = HALDoc(self.test_doc)
        self.assertEqual(haldoc.links.self.href,
                         self.test_doc['_links']['self']['href'])

    def test_exception_raised_if_no_href_in_link(self):
        no_href = {'_links': {'nohref': {'title': 'A Title'}}}
        with self.assertRaises(ValueError):
            HALDoc(no_href)

    def test_links_should_allow_lists(self):
        doc = {
            '_links': {
                'self': {'href': 'http://example.com'},
                'children': [
                    {'href': 'http://example.com/children/1'},
                    {'href': 'http://example.com/children/2'}
                ]
            }
        }
        haldoc = HALDoc(doc)
        self.assertEquals(haldoc.links.children[0].href,
                          doc['_links']['children'][0]['href'])
        self.assertEquals(haldoc.links.children[1].href,
                          doc['_links']['children'][1]['href'])


class ChainTestCase(TestCase):

    def setUp(self):
        self.unit = Unit(name='C')
        self.unit.save()
        self.temp_metric = Metric(name='temperature')
        self.temp_metric.save()
        self.setpoint_metric = Metric(name='setpoint')
        self.setpoint_metric.save()
        self.geo_locations = [
            GeoLocation(elevation=50, latitude=42.847, longitude=72.917),
            GeoLocation(elevation=-23.8, latitude=40.847, longitude=42.917)
        ]
        for loc in self.geo_locations:
            loc.save()
        self.metadata = []
        self.sites = [
            Site(name='Test Site 1',
                 geo_location=self.geo_locations[0],
                 raw_zmq_stream='tcp://example.com:8372'),
            Site(name='Test Site 2',
                 geo_location=self.geo_locations[1],
                 raw_zmq_stream='tcp://example.com:8172')
        ]
        for site in self.sites:
            site.save()
            self.metadata.append(Metadata(key="Test",
                                          value="Test Metadata 1",
                                          timestamp=now().isoformat(),
                                          content_object=site))
            self.metadata.append(Metadata(key="Test",
                                          value="Test Metadata 2",
                                          timestamp=now().isoformat(),
                                          content_object=site))

        num_devices = 2 * len(self.sites)
        self.devices = [Device(name='Thermostat %d' % i,
                               site=self.sites[i % len(self.sites)])
                        for i in range(0, num_devices)]
        num_people = 2 * len(self.sites)
        # self.people = [Person(first_name='John',
        #                       last_name = 'Doe %d' % i,
        #                       site=self.sites[i % len(self.sites)])
        #                for i in range(0, num_people)]
        # for person in self.people:
        #     person.save()
        self.sensors = []
        for device in self.devices:
            device.save()
            self.metadata.append(Metadata(key="Test",
                                          value="Test Metadata 1",
                                          timestamp=now().isoformat(),
                                          content_object=device))
            self.metadata.append(Metadata(key="Test",
                                          value="Test Metadata 2",
                                          timestamp=now().isoformat(),
                                          content_object=device))

            self.sensors.append(ScalarSensor(device=device,
                                             metric=self.temp_metric,
                                             unit=self.unit))
            self.sensors.append(ScalarSensor(device=device,
                                             metric=self.setpoint_metric,
                                             unit=self.unit))
        self.scalar_data = []
        for sensor in self.sensors:
            sensor.save()
            self.metadata.append(Metadata(key="Test",
                                          value="Test Metadata 1",
                                          timestamp=now().isoformat(),
                                          content_object=sensor))
            self.metadata.append(Metadata(key="Test",
                                          value="Test Metadata 1",
                                          timestamp=now().isoformat(),
                                          content_object=sensor))

            self.scalar_data.append({
                'sensor': sensor,
                'timestamp': now() - timedelta(minutes=2),
                'value': 22.0})
            self.scalar_data.append({
                'sensor': sensor,
                'timestamp': now() - timedelta(minutes=1),
                'value': 23.0})
        for data in self.scalar_data:
            resources.influx_client.post_data(data['sensor'].device.site.id,
                                              data['sensor'].device.id,
                                              data['sensor'].id,
                                              data['value'],
                                              data['timestamp'])
        for metadata in self.metadata:
            metadata.save()


    def get_resource(self, url, mime_type='application/hal+json',
                     expect_status_code=HTTP_STATUS_SUCCESS,
                     check_mime_type=True,
                     check_vary_header=True,
                     should_cache=None):
        accept_header = mime_type + ',' + ACCEPT_TAIL
        response = self.client.get(url,
                                   HTTP_ACCEPT=accept_header,
                                   HTTP_HOST='localhost')
        self.assertEqual(response.status_code, expect_status_code)
        if check_mime_type:
            self.assertEqual(response['Content-Type'], mime_type)
        if check_vary_header:
            # all resource responses should have the "Vary" header, which tells
            # intermediate caching servers that it needs to include the Accept
            # header in its cache lookup key
            self.assertIn(response['Vary'], "Accept")
        if should_cache is not None:
            if should_cache:
                self.assertIn("max-age", response['Cache-Control'])
            else:
                self.assertFalse(response.has_header('Cache-Control'))
        if response['Content-Type'] == 'application/hal+json':
            return HALDoc(json.loads(response.content))
        elif response['Content-Type'] == 'application/json':
            return json.loads(response.content)
        else:
            return response.content

    def create_resource(self, url, resource):
        return self.post_resource(url, resource, HTTP_STATUS_CREATED)

    def update_resource(self, url, resource):
        return self.post_resource(url, resource, HTTP_STATUS_SUCCESS)

    def post_resource(self, url, resource, expected_status):
        mime_type = 'application/hal+json'
        accept_header = mime_type + ',' + ACCEPT_TAIL
        response = self.client.post(url, json.dumps(resource),
                                    content_type=mime_type,
                                    HTTP_ACCEPT=accept_header,
                                    HTTP_HOST='localhost')
        self.assertEqual(response.status_code, expected_status)
        self.assertEqual(response['Content-Type'], mime_type)
        if mime_type == 'application/hal+json':
            response_data = json.loads(response.content)
            if isinstance(response_data, list):
                return [HALDoc(d) for d in response_data]
            else:
                return HALDoc(response_data)
        elif mime_type == 'application/json':
            return json.loads(response.content)
        else:
            return response.content

    def get_sites(self, **kwargs):
        root = self.get_resource(BASE_API_URL)
        sites_url = root.links['ch:sites'].href
        return self.get_resource(sites_url, **kwargs)

    def get_a_site(self, **kwargs):
        '''GETs a site through the API for testing'''
        sites = self.get_sites()
        self.assertIn('items', sites.links)
        self.assertIn('href', sites.links.items[0])
        site_url = sites.links.items[0].href
        # following the link like a good RESTful client
        return self.get_resource(site_url, **kwargs)

    def get_devices(self, **kwargs):
        site = self.get_a_site()
        return self.get_resource(site.links['ch:devices'].href, **kwargs)

    # def get_a_person(self):
    #     site = self.get_a_site()
    #     people = self.get_resource(site.links['ch:people'].href)
    #     return self.get_resource(people.links['items'][0].href)
    #
    def get_a_device(self, **kwargs):
        '''GETs a device through the API for testing'''
        devices = self.get_devices()
        return self.get_resource(devices.links.items[0].href, **kwargs)

    def get_sensors(self, **kwargs):
        device = self.get_a_device()
        return self.get_resource(device.links['ch:sensors'].href, **kwargs)

    def get_a_sensor(self, **kwargs):
        sensors = self.get_sensors()
        return self.get_resource(sensors.links.items[0].href, **kwargs)

    def create_a_sensor_of_type(self, sensor_type):
        device = self.get_a_device()
        sensors = self.get_resource(device.links['ch:sensors'].href)
        sensor_url = sensors.links['createForm'].href

        new_sensor = {
            'sensor-type': sensor_type,
            'metric': 'rfid',
            'unit': 'N/A',
        }
        return self.create_resource(sensor_url, new_sensor)

    def get_a_sensor_of_type(self, sensor_type):
        sensors = self.get_sensors()
        for link in sensors.links.items:
            sensor = self.get_resource(link.href)
            if sensor['sensor-type'] == sensor_type:
                return sensor
        return self.create_a_sensor_of_type(sensor_type)

    def get_metadata(self):
        site = self.get_a_site()
        return self.get_resource(site.links['ch:metadata'].href)

    def get_site_device_sensor(self):
        return [self.get_a_site(), self.get_a_device(), self.get_a_sensor()]


class ScalarSensorDataTest(ChainTestCase):

    def test_data_can_be_added(self):
        data = {
            'sensor': self.sensors[0],
            'value': 25,
            'timestamp': now()
        }
        resources.influx_client.post_data(data['sensor'].device.site.id,
                                          data['sensor'].device.id,
                                          data['sensor'].id,
                                          data['value'],
                                          data['timestamp'])
        self.assertEqual(data['value'], 25)


class BasicHALJSONTests(ChainTestCase):

    def test_response_with_accept_hal_json_should_return_hal_json(self):
        response = self.client.get(BASE_API_URL,
                                   HTTP_ACCEPT='application/hal+json')
        self.assertEqual(response.status_code, HTTP_STATUS_SUCCESS)
        self.assertEqual(response['Content-Type'], 'application/hal+json')


class CacheTests(ChainTestCase):

    def test_site_is_not_cached(self):
        site = self.get_a_site(should_cache=False)

    def test_device_is_not_cached(self):
        device = self.get_a_device(should_cache=False)

    def test_site_summary_is_cached(self):
        site = self.get_a_site()
        summary = self.get_resource(site.links['ch:siteSummary'].href,
                                    should_cache=True)

class DefaultMIMETests(ChainTestCase):

    def test_root_should_supply_json_if_no_accept_header(self):
        data = self.get_resource(BASE_API_URL)
        sites_coll = data.links['ch:sites']
        response = self.client.get(sites_coll.href, HTTP_HOST='localhost')
        self.assertEqual(response.status_code, HTTP_STATUS_SUCCESS)
        self.assertEqual(response['Content-Type'], "application/json")


class SafePostTests(ChainTestCase):

    def test_lack_of_json_data_in_edit_should_not_crash_server(self):
        site = self.get_a_site()
        edit_href = site.links.editForm.href
        edit_form = self.get_resource(edit_href)
        new_site = obj_from_filled_schema(edit_form)
        new_site['name'] = 'Some New Name'
        new_site['rawZMQStream'] = 'tcp://newexample.com:7162'

        mime_type = 'application/hal+json'
        accept_header = mime_type + ',' + ACCEPT_TAIL
        response = None
        try:
            response = self.client.post(edit_href, "",
                                        content_type=mime_type,
                                        HTTP_ACCEPT=accept_header,
                                        HTTP_HOST='localhost')
        except ValueError:
            self.assertTrue(False)  # lack of JSON crashed the server
        self.assertEqual(response.status_code, HTTP_STATUS_BAD_REQUEST)
        self.assertEqual(response['Content-Type'], "application/json")

    def test_lack_of_json_data_in_create_should_not_crash_server(self):
        sites = self.get_sites()

        mime_type = 'application/hal+json'
        accept_header = mime_type + ',' + ACCEPT_TAIL
        response = None
        try:
            response = self.client.post(sites.links.createForm.href,
                                        "bad json",
                                        content_type=mime_type,
                                        HTTP_ACCEPT=accept_header,
                                        HTTP_HOST='localhost')
        except ValueError:
            self.assertTrue(False)  # lack of JSON crashed the server
        self.assertEqual(response.status_code, HTTP_STATUS_BAD_REQUEST)
        self.assertEqual(response['Content-Type'], "application/json")

    def test_ambiguous_timestamps_should_not_crash_server(self):
        sensor = self.get_a_sensor()
        sensor_data = self.get_resource(
            sensor.links['ch:dataHistory'].href)
        data_url = sensor_data.links.createForm.href
        data = {
            'value': 20,
            'timestamp': datetime(2015, 11, 1, 1, 0, 0).isoformat()
            }
        mime_type = 'application/hal+json'
        accept_header = mime_type + ',' + ACCEPT_TAIL
        try:
            response = self.client.post(data_url,
                                        json.dumps(data),
                                        content_type=mime_type,
                                        HTTP_ACCEPT=accept_header,
                                        HTTP_HOST='localhost')
        except AmbiguousTimeError:
            self.assertTrue(False)
        self.assertEqual(response.status_code, HTTP_STATUS_BAD_REQUEST)
        self.assertEqual(response['Content-Type'], "application/json")

class ApiRootTests(ChainTestCase):

    def test_root_should_have_self_rel(self):
        root = self.get_resource(BASE_API_URL,
                                 mime_type='application/hal+json')
        self.assertIn('self', root.links)
        self.assertIn('href', root.links.self)

    def test_root_should_have_curies_link(self):
        data = self.get_resource(BASE_API_URL)
        curies = data.links.curies
        self.assertEqual(curies[0].name, 'ch')
        self.assertRegexpMatches(curies[0].href, 'http://.*')

    def test_root_should_have_sites_link(self):
        data = self.get_resource(BASE_API_URL)
        sites_coll = data.links['ch:sites']
        self.assertRegexpMatches(sites_coll.href, 'http://.*' + SITES_URL)


class ApiSitesTests(ChainTestCase):

    def test_nonexistant_site_should_return_404(self):
        sites = self.get_sites()
        non_existant_site_url = sites.links.items[0].href
        site_urls = set((item.href for item in sites.links.items))
        while non_existant_site_url in site_urls:
            non_existant_site_url += str(random.randint(100, 999))
        self.get_resource(non_existant_site_url, expect_status_code=HTTP_STATUS_NOT_FOUND, \
            check_mime_type=False, check_vary_header=False)

    def test_sites_coll_should_have_self_rel(self):
        sites = self.get_sites()
        self.assertIn('href', sites.links.self)

    def test_site_should_have_curies_link(self):
        site = self.get_a_site()
        curies = site.links.curies
        self.assertEqual(curies[0].name, 'ch')
        self.assertRegexpMatches(curies[0].href, 'http://.*')

    def test_sites_coll_should_have_curies_link(self):
        sites = self.get_sites()
        curies = sites.links.curies
        self.assertEqual(curies[0].name, 'ch')
        self.assertRegexpMatches(curies[0].href, 'http://.*')

    def test_sites_should_have_createform_link(self):
        sites = self.get_sites()
        self.assertIn('createForm', sites.links)
        self.assertIn('href', sites.links.createForm)
        self.assertEqual(sites.links.createForm.title, 'Create Site')

    def test_sites_should_have_items_link(self):
        sites = self.get_sites()
        self.assertIn('items', sites.links)

    def test_sites_links_should_have_title(self):
        sites = self.get_sites()
        self.assertIn(sites.links.items[0].title,
                      [s.name for s in self.sites])

    def test_sites_collection_should_have_total_count(self):
        sites = self.get_sites()
        self.assertEqual(sites.totalCount, len(self.sites))

    def test_site_should_have_self_link(self):
        site = self.get_a_site()
        self.assertIn('href', site.links.self)

    def test_site_should_have_stream_href(self):
        site = self.get_a_site()
        stream_href = site.links['ch:websocketStream'].href
        self.assertIn('ws://', stream_href)

    def test_site_should_have_name(self):
        site = self.get_a_site()
        self.assertIn(site.name, [s.name for s in self.sites])

    def test_site_should_have_devices_link(self):
        site = self.get_a_site()
        self.assertIn('ch:devices', site.links)
        self.assertIn('href', site.links['ch:devices'])
        devices = self.get_resource(site.links['ch:devices'].href)
        db_site = Site.objects.get(name=site.name)
        self.assertEqual(devices.totalCount, db_site.devices.count())

    def test_site_should_have_geolocation(self):
        site = self.get_a_site()
        self.assertIn('geoLocation', site)
        self.assertIn('elevation', site.geoLocation)
        self.assertIn(site.geoLocation['elevation'],
                      [l.elevation for l in self.geo_locations])
        self.assertIn('latitude', site.geoLocation)
        self.assertIn(site.geoLocation['latitude'],
                      [l.latitude for l in self.geo_locations])
        self.assertIn('longitude', site.geoLocation)
        self.assertIn(site.geoLocation['longitude'],
                      [l.longitude for l in self.geo_locations])

    def test_site_should_have_tidmarsh_zmq_link(self):
        site = self.get_a_site()
        self.assertIn('rawZMQStream', site.links)
        self.assertIn('href', site.links.rawZMQStream)

    def test_sites_should_be_postable(self):
        new_site = {
            'geoLocation': {
                'latitude': 42.360461,
                'longitude': -71.087347,
                'elevation': 12
            },
            'name': 'MIT Media Lab',
            'rawZMQStream': 'tcp://example.com:8372'
        }
        sites = self.get_sites()
        response = self.create_resource(sites.links.createForm.href, new_site)
        db_obj = Site.objects.get(name='MIT Media Lab')
        self.assertEqual(new_site['name'], response.name)
        self.assertEqual(new_site['name'], db_obj.name)
        self.assertEqual(new_site['name'], response.links['self'].title)
        self.assertEqual(new_site['rawZMQStream'],
                         response.links.rawZMQStream.href)
        self.assertEqual(new_site['rawZMQStream'],
                         db_obj.raw_zmq_stream)
        for field in ['latitude', 'longitude', 'elevation']:
            self.assertEqual(new_site['geoLocation'][field],
                             response.geoLocation[field])
            self.assertEqual(new_site['geoLocation'][field],
                             getattr(db_obj.geo_location, field))

    def test_site_create_form_should_return_schema(self):
        sites = self.get_sites()
        site_schema = self.get_resource(sites.links.createForm.href)
        self.assertIn('type', site_schema)
        self.assertEquals(site_schema['type'], 'object')
        self.assertIn('properties', site_schema)
        self.assertIn('name', site_schema['properties'])
        self.assertEquals(site_schema['properties']['name'],
                          {'type': 'string', 'title': 'name', 'minLength': 1})
        self.assertIn('rawZMQStream', site_schema['properties'])
        self.assertEquals(site_schema['properties']['rawZMQStream'],
                          {'type': 'string',
                           'format': 'uri',
                           'title': 'rawZMQStream'})
        self.assertIn('required', site_schema)
        self.assertEquals(site_schema['required'], ['name'])

    def test_site_schema_should_include_geolocation(self):
        sites = self.get_sites()
        site_schema = self.get_resource(sites.links.createForm.href)
        self.assertIn('properties', site_schema)
        self.assertIn('geoLocation', site_schema['properties'])
        self.assertEquals(site_schema['properties']['geoLocation'], {
            'type': 'object',
            'title': 'geoLocation',
            'properties': {
                'latitude': {'type': 'number', 'title': 'latitude'},
                'longitude': {'type': 'number', 'title': 'longitude'},
                'elevation': {'type': 'number', 'title': 'elevation'}
            },
            'required': ['latitude', 'longitude']
        })

    def test_site_should_have_edit_link(self):
        site = self.get_a_site()
        self.assertIn('editForm', site.links)

    def test_site_edit_view_should_have_schema_with_defaults(self):
        site = self.get_a_site()
        edit_form = self.get_resource(site.links.editForm.href)
        self.assertEquals(edit_form['type'], 'object')
        self.assertEquals(edit_form['properties']['name']['default'],
                          site.name)
        self.assertEquals(edit_form['properties']['rawZMQStream']['default'],
                          site.links.rawZMQStream.href)
        edit_loc = edit_form['properties']['geoLocation']
        self.assertEquals(edit_loc['properties']['latitude']['default'],
                          site.geoLocation['latitude'])
        self.assertEquals(edit_loc['properties']['longitude']['default'],
                          site.geoLocation['longitude'])

    def test_sites_should_be_editable(self):
        site = self.get_a_site()
        edit_href = site.links.editForm.href
        edit_form = self.get_resource(edit_href)
        new_site = obj_from_filled_schema(edit_form)
        new_site['name'] = 'Some New Name'
        new_site['rawZMQStream'] = 'tcp://newexample.com:7162'
        response = self.update_resource(edit_href, new_site)
        reget = self.get_resource(site.links.self.href)
        self.assertEqual(response.name, new_site['name'])
        self.assertEqual(response.geoLocation['latitude'],
                         site.geoLocation['latitude'])
        self.assertEqual(response.links.rawZMQStream.href,
                         new_site['rawZMQStream'])
        self.assertEqual(response, reget)

    def test_geolocation_should_be_addable_to_site(self):
        site = {
            'name': 'Geolocate Add Test',
        }
        sites = self.get_sites()
        site_response = self.create_resource(sites.links.createForm.href,
                                             site)
        edit_form = self.get_resource(site_response.links.editForm.href)
        new_site = obj_from_filled_schema(edit_form)
        new_site['geoLocation'] = {
            'latitude': 42.360461,
            'longitude': -71.087347
        }
        self.update_resource(site_response.links.editForm.href, new_site)
        new_site_response = self.get_resource(site_response.links.self.href)
        self.assertEqual(new_site_response.geoLocation['latitude'],
                         new_site['geoLocation']['latitude'])
        self.assertEqual(new_site_response.geoLocation['longitude'],
                         new_site['geoLocation']['longitude'])

    def test_site_should_have_summary_link(self):
        site = self.get_a_site()
        self.assertIn('ch:siteSummary', site.links)

    def test_site_summary_should_have_devices(self):
        site = self.get_a_site()
        device = self.get_a_device()
        summary = self.get_resource(site.links['ch:siteSummary'].href)
        self.assertIn(device.name, [dev['name'] for dev in
                                    summary.devices])

    def test_site_summary_devices_should_not_have_rels(self):
        site = self.get_a_site()
        summary = self.get_resource(site.links['ch:siteSummary'].href)
        summary_dev = summary.devices[0]
        self.assertNotIn('_links', summary_dev)
        self.assertNotIn('_embedded', summary_dev)

    def test_site_summary_should_have_sensors(self):
        site = self.get_a_site()
        summary = self.get_resource(site.links['ch:siteSummary'].href)
        summary_dev = summary.devices[0]
        self.assertIn('metric', summary_dev['sensors'][0])

    def test_site_summary_should_have_empty_data(self):
        site = self.get_a_site()
        summary = self.get_resource(site.links['ch:siteSummary'].href)
        summary_dev = summary.devices[0]
        self.assertEqual(0, len(summary_dev['sensors'][0]['data']))

    def test_site_summary_resources_should_have_href(self):
        site = self.get_a_site()
        summary = self.get_resource(site.links['ch:siteSummary'].href)
        summary_dev = summary.devices[0]
        self.assertIn('href', summary_dev)
        self.assertIn('href', summary_dev['sensors'][0])


class ApiDeviceTests(ChainTestCase):

    def test_nonexistant_device_should_return_404(self):
        devices = self.get_devices()
        self.get_resource(devices.links.items[0].href \
            + "NONEXISTANT_RESOURCE", expect_status_code=HTTP_STATUS_NOT_FOUND, \
            check_mime_type=False, check_vary_header=False)

    def test_device_should_have_sensors_link(self):
        device = self.get_a_device()
        self.assertIn('ch:sensors', device.links)
        self.assertEqual('Sensors', device.links['ch:sensors'].title)

    def test_device_should_have_site_link(self):
        device = self.get_a_device()
        self.assertIn('ch:site', device.links)

    def test_device_should_have_curies_link(self):
        device = self.get_a_device()
        curies = device.links.curies
        self.assertEqual(curies[0].name, 'ch')
        self.assertRegexpMatches(curies[0].href, 'http://.*')

    def test_devices_coll_should_have_curies_link(self):
        devices = self.get_devices()
        curies = devices.links.curies
        self.assertEqual(curies[0].name, 'ch')
        self.assertRegexpMatches(curies[0].href, 'http://.*')

    def test_device_should_be_postable_to_a_site(self):
        site = self.get_a_site()
        devices = self.get_resource(site.links['ch:devices'].href)
        dev_url = devices.links.createForm.href
        new_device = {
            "building": "E14",
            "description": "A great device",
            "floor": "5",
            "name": "Unit Test Thermostat 42",
            "room": "E14-548R"
        }
        response = self.create_resource(dev_url, new_device)
        self.assertEqual(new_device['name'], response.links['self'].title)
        # make sure that a device now exists with the right name
        db_device = Device.objects.get(name=new_device['name'])
        # make sure that the device is set up in the right site
        db_site = Site.objects.get(name=site['name'])
        self.assertEqual(db_device.site, db_site)

    def test_device_create_form_should_return_schema(self):
        devices = self.get_devices()
        device_schema = self.get_resource(devices.links.createForm.href)
        self.assertIn('type', device_schema)
        self.assertEquals(device_schema['type'], 'object')
        self.assertIn('properties', device_schema)
        self.assertIn('name', device_schema['properties'])
        self.assertEquals(device_schema['properties']['name'],
                          {'type': 'string',
                           'title': 'name',
                           'minLength': 1})
        for field_name in ['description', 'building', 'floor', 'room']:
            self.assertIn(field_name, device_schema['properties'])
            self.assertEquals(device_schema['properties'][field_name],
                              {'type': 'string',
                               'title': field_name})
        self.assertIn('required', device_schema)
        self.assertEquals(device_schema['required'], ['name'])

    def test_device_schema_should_include_geolocation(self):
        devices = self.get_devices()
        device_schema = self.get_resource(devices.links.createForm.href)
        self.assertIn('properties', device_schema)
        self.assertIn('geoLocation', device_schema['properties'])
        self.assertEquals(device_schema['properties']['geoLocation'], {
            'type': 'object',
            'title': 'geoLocation',
            'properties': {
                'latitude': {'type': 'number', 'title': 'latitude'},
                'longitude': {'type': 'number', 'title': 'longitude'},
                'elevation': {'type': 'number', 'title': 'elevation'}
            },
            'required': ['latitude', 'longitude']
        })

    def test_posting_device_should_send_zmq_msgs(self):
        fake_zmq_socket.clear()

        site = self.get_a_site()
        devices = self.get_resource(site.links['ch:devices'].href)
        dev_url = devices.links.createForm.href
        new_device = {"name": "Unit Test Thermostat 42"}
        self.create_resource(dev_url, new_device)
        db_device = Device.objects.get(name=new_device['name'])

        # make sure that a message got sent to all the appropriate tags
        stream_tags = [
            'site-%d' % db_device.site_id,
            'device-%d' % db_device.id
        ]
        for tag in stream_tags:
            self.assertEqual(1, len(fake_zmq_socket.sent_msgs[tag]))
            self.assertEqual(new_device['name'],
                             fake_zmq_socket.sent_msgs[tag][0]['name'])

    def test_device_should_be_deactivatable(self):
        device = self.get_a_device()
        edit_href = device.links.editForm.href
        device['active'] = False
        response = self.update_resource(edit_href, device)
        self.assertEqual(response.active, device['active'])


class ApiScalarSensorTests(ChainTestCase):

    def test_sensors_should_be_postable_to_existing_device(self):
        device = self.get_a_device()
        sensors = self.get_resource(device.links['ch:sensors'].href)
        sensor_url = sensors.links['createForm'].href

        new_sensor = {
            'metric': 'Bridge Length',
            'unit': 'Smoots',
        }
        self.create_resource(sensor_url, new_sensor)
        db_sensor = ScalarSensor.objects.get(metric__name='Bridge Length',
                                             device__name=device.name)
        self.assertEqual('Smoots', db_sensor.unit.name)

    def test_sensors_should_be_postable_to_newly_posted_device(self):
        site = self.get_a_site()
        devices = self.get_resource(site.links['ch:devices'].href)

        new_device = {
            "building": "E14",
            "description": "A great device",
            "floor": "5",
            "name": "Unit Test Thermostat 49382",
            "room": "E14-548R"
        }
        device = self.create_resource(devices.links['createForm'].href,
                                      new_device)

        sensors = self.get_resource(device.links['ch:sensors'].href)
        new_sensor = {
            'metric': 'Beauty',
            'unit': 'millihelen',
        }
        response = self.create_resource(sensors.links['createForm'].href, new_sensor)
        db_sensor = ScalarSensor.objects.get(metric__name='Beauty',
                                             device__name=device.name)
        self.assertEqual('millihelen', db_sensor.unit.name)
        self.assertEqual('Beauty', response.links['self'].title)

    def test_sensor_should_have_data_url(self):
        sensor = self.get_a_sensor()
        self.assertIn('ch:dataHistory', sensor.links)

    def test_sensor_should_have_parent_link(self):
        sensor = self.get_a_sensor()
        self.assertIn('ch:device', sensor.links)

    def test_sensor_should_have_value_and_timestamp(self):
        sensor = self.get_a_sensor()
        self.assertIn('value', sensor)
        self.assertIn('updated', sensor)

    def test_sensor_should_have_float_datatype(self):
        sensor = self.get_a_sensor()
        self.assertIn('dataType', sensor)
        self.assertEquals(sensor.dataType, 'float')

    def test_sensor_should_be_editable(self):
        sensor = self.get_a_sensor()
        edit_href = sensor.links.editForm.href
        edit_form = self.get_resource(edit_href)
        new_sensor = obj_from_filled_schema(edit_form)
        new_sensor['metric'] = 'fuzziness'
        response = self.update_resource(edit_href, new_sensor)
        self.assertEqual(response.metric, new_sensor['metric'])
        self.assertEqual(response.unit, new_sensor['unit'])

    def test_sensor_should_be_deactivatable(self):
        sensor = self.get_a_sensor()
        edit_href = sensor.links.editForm.href
        sensor['active'] = False
        response = self.update_resource(edit_href, sensor)
        self.assertEqual(response.active, sensor['active'])


# class ApiPresenceSensorTests(ChainTestCase):
#     def test_presence_sensors_should_be_postable_to_existing_device(self):
#         device = self.get_a_device()
#         sensors = self.get_resource(device.links['ch:sensors'].href)
#         sensor_url = sensors.links['createForm'].href
#
#         new_sensor = {
#             'sensor-type': 'presence',
#             'metric': 'rfid',
#             'unit': 'N/A',
#         }
#         new_sensor_res = self.create_resource(sensor_url, new_sensor)
#         new_sensor_link = new_sensor_res['_links']['self']['href']
#         db_sensor = PresenceSensor.objects.get(metric__name='rfid',
#                                                device__name=device.name)
#         self.assertTrue(db_sensor is not None)
#         self.assertEqual(new_sensor_res.links['self'].title, new_sensor['metric'])
#
#         # Reload the list of sensors:
#         sensors = self.get_resource(device.links['ch:sensors'].href)
#
#         # Check to see if new sensor included in the list:
#         found_self = False
#         for link in sensors.links['items']:
#             if link['href'] == new_sensor_link:
#                 found_self = True
#                 break
#         self.assertTrue(found_self)
#
#     def test_presence_sensors_should_be_postable_to_newly_posted_device(self):
#         site = self.get_a_site()
#         devices = self.get_resource(site.links['ch:devices'].href)
#
#         new_device = {
#             "building": "E14",
#             "description": "Another great device",
#             "floor": "5",
#             "name": "Unit Test Presence Sensor 49382",
#             "room": "E14-548R"
#         }
#         device = self.create_resource(devices.links['createForm'].href,
#                                       new_device)
#
#         sensors = self.get_resource(device.links['ch:sensors'].href)
#         sensor_url = sensors.links['createForm'].href
#         new_sensor = {
#             'sensor-type': 'presence',
#             'metric': 'rfid',
#             'unit': 'N/A',
#         }
#         new_sensor_res = self.create_resource(sensor_url, new_sensor)
#         new_sensor_link = new_sensor_res['_links']['self']['href']
#         db_sensor = PresenceSensor.objects.get(metric__name='rfid',
#                                                device__name=device.name)
#         self.assertTrue(db_sensor is not None)
#
#         # Reload the list of sensors:
#         sensors = self.get_resource(device.links['ch:sensors'].href)
#
#         # Check to see if new sensor included in the list:
#         found_self = False
#         for link in sensors.links['items']:
#             if link['href'] == new_sensor_link:
#                 found_self = True
#                 break
#         self.assertTrue(found_self)
#
#     def test_presence_sensor_should_have_data_url(self):
#         sensor = self.get_a_sensor_of_type('presence')
#         self.assertTrue(sensor is not None)
#         self.assertIn('ch:dataHistory', sensor.links)
#
#     def test_presence_sensor_should_have_parent_link(self):
#         sensor = self.get_a_sensor_of_type('presence')
#         self.assertTrue(sensor is not None)
#         self.assertIn('ch:device', sensor.links)
#
#     '''def test_sensor_should_have_value_and_timestamp(self):
#         sensor = self.get_a_sensor_of_type('presence')
#         self.assertTrue(sensor is not None)
#         self.assertIn('value', sensor)
#         self.assertIn('updated', sensor)'''
#
#     def test_presence_sensor_should_have_presence_datatype(self):
#         sensor = self.get_a_sensor_of_type('presence')
#         self.assertTrue(sensor is not None)
#         self.assertIn('dataType', sensor)
#         self.assertEquals(sensor.dataType, 'presence')
#
#     def test_presence_sensor_should_be_editable(self):
#         sensor = self.get_a_sensor_of_type('presence')
#         self.assertTrue(sensor is not None)
#         edit_href = sensor.links.editForm.href
#         edit_form = self.get_resource(edit_href)
#         new_sensor = obj_from_filled_schema(edit_form)
#         new_sensor['unit'] = 'rfid2'
#         self.update_resource(edit_href, new_sensor)
#
#
# class ApiPresenceSensorDataTests(ChainTestCase):
#     def setUp(self):
#         super(ApiPresenceSensorDataTests, self).setUp()
#         # make sure there's data in the first presence sensor
#         sensor = self.get_a_sensor_of_type('presence')
#         data = self.get_resource(sensor.links['ch:dataHistory'].href)
#         create_url = data.links['createForm'].href
#         new_data = {
#             'present': True,
#             'person': self.get_a_person().links['self'].href
#         }
#         self.create_resource(create_url, new_data)
#
#     def test_presence_data_should_have_edit_form(self):
#         sensor = self.get_a_sensor_of_type('presence')
#         all_data = self.get_resource(sensor.links['ch:dataHistory'].href)
#         data = self.get_resource(all_data.links['items'][0].href)
#         edit_href = data.links['editForm'].href
#         self.get_resource(edit_href)
#
#     def test_presence_data_should_be_editable(self):
#         sensor = self.get_a_sensor_of_type('presence')
#         all_data = self.get_resource(sensor.links['ch:dataHistory'].href)
#         data = self.get_resource(all_data.links['items'][0].href)
#         edit_href = data.links['editForm'].href
#         schema = self.get_resource(edit_href)
#         new_data = obj_from_filled_schema(schema)
#         new_data['present'] = not new_data['present']
#         self.update_resource(edit_href, new_data)


class ApiScalarSensorDataTests(ChainTestCase):

    def test_sensor_data_should_have_timestamp_and_value(self):
        sensor = self.get_a_sensor()
        sensor_data = self.get_resource(
            sensor.links['ch:dataHistory'].href)
        self.assertIn('timestamp', sensor_data.data[0])
        self.assertIn('value', sensor_data.data[0])

    def test_sensor_data_should_have_data_type(self):
        sensor = self.get_a_sensor()
        sensor_data = self.get_resource(
            sensor.links['ch:dataHistory'].href)
        self.assertIn('dataType', sensor_data)
        self.assertEqual('float', sensor_data.dataType)

    def test_sensor_data_should_be_postable(self):
        device = self.get_a_device()
        sensor = self.get_a_sensor()
        sensor_data = self.get_resource(
            sensor.links['ch:dataHistory'].href)
        data_url = sensor_data.links.createForm.href
        timestamp = make_aware(datetime(2013, 1, 1, 0, 0, 0), utc)
        data = {
            'value': 23,
            'timestamp': timestamp.isoformat()
        }
        sensor_id = re.search(r'[^=]*$', data_url).group(0)
        self.create_resource(data_url, data)
        filters = {
            'sensor_id': sensor_id,
            'timestamp__gte': timestamp,
            'timestamp__lt': timestamp + timedelta(seconds=0.1)
        }
        db_data = resources.influx_client.get_sensor_data(filters)[0]
        self.assertEqual(db_data['value'], data['value'])

    def test_lists_of_sensor_data_should_be_postable(self):
        device = self.get_a_device()
        sensor = self.get_a_sensor()
        sensor_data = self.get_resource(
            sensor.links['ch:dataHistory'].href)
        data_url = sensor_data.links.createForm.href
        sensor_id = re.search(r'[^=]*$', data_url).group(0)
        basetime = make_aware(datetime(2013, 1, 1, 0, 0, 0), utc)
        timestamps = [basetime + timedelta(seconds=i) for i in range(0, 3)]
        values = range(0, 3)
        data = [{
            'value': value,
            'timestamp': timestamp.isoformat()
        } for value, timestamp in zip(values, timestamps)]
        self.create_resource(data_url, data)
        filters = {
            'sensor_id': sensor_id
        }
        for i in range(0, 3):
            filters['timestamp__gte'] = timestamps[i]
            filters['timestamp__lt'] = timestamps[i] + timedelta(seconds=0.1)
            db_data = resources.influx_client.get_sensor_data(filters)[0]
            self.assertEqual(db_data['value'], values[i])

    def test_posting_data_should_send_zmq_msgs(self):
        fake_zmq_socket.clear()
        sensor = self.get_a_sensor()
        device = self.get_resource(
            sensor.links['ch:device'].href)
        db_sensor = ScalarSensor.objects.get(
            metric__name=sensor.metric,
            device__name=device.name)
        sensor_data = self.get_resource(
            sensor.links['ch:dataHistory'].href)
        data_url = sensor_data.links.createForm.href
        data = {'value': 23}
        self.create_resource(data_url, data)

        # make sure that a message got sent to all the appropriate tags
        stream_tags = [
            'site-%d' % db_sensor.device.site_id,
            'device-%d' % db_sensor.device_id,
            'sensor-%d' % db_sensor.id
        ]
        for tag in stream_tags:
            self.assertEqual(1, len(fake_zmq_socket.sent_msgs[tag]))
            self.assertEqual(data['value'],
                             fake_zmq_socket.sent_msgs[tag][0]['value'])
            self.assertEqual(sensor.links['self'].href,
                             fake_zmq_socket.sent_msgs[tag][0]['_links']['ch:sensor']['href'])


    def test_posting_data_should_sanitize_args_for_response(self):
        fake_zmq_socket.clear()
        sensor = self.get_a_sensor()
        sensor_data = self.get_resource(
            sensor.links['ch:dataHistory'].href)
        data_url = sensor_data.links.createForm.href
        data = {'value': "23"}
        response = self.create_resource(data_url, data)
        self.assertEqual(response.value, 23.0)
        self.assertEqual(type(response.value), float)

    def test_collection_links_should_not_have_page_info(self):
        # we want to allow the server to just give the default pagination when
        # the client is just following links around
        sensor = self.get_a_sensor()
        self.assertNotIn('offset', sensor.links['ch:dataHistory'].href)
        self.assertNotIn('limit', sensor.links['ch:dataHistory'].href)

    def test_paginated_data_can_be_requested_with_only_limit(self):
        site = self.get_a_site()
        db_site = Site.objects.get(name=site.name)
        Device.objects.bulk_create(
            [Device(name="Test ScalarSensor %d" % i, site=db_site)
             for i in range(1500)])
        datapage = self.get_resource(
            site.links["ch:devices"].href + "&limit=20")
        self.assertEqual(20, len(datapage.links['items']))
        datapage = self.get_resource(
            site.links["ch:devices"].href + "&limit=1000")
        self.assertEqual(1000, len(datapage.links['items']))

    def test_sensor_data_timestamp_edge_cases(self):
        sensor = self.get_a_sensor()
        self.get_resource(
            sensor.links['ch:dataHistory'].href +
            "&timestamp__gte=NaN&timestamp__lt=NaN",
            expect_status_code=HTTP_STATUS_BAD_REQUEST,
            check_mime_type=False,
            check_vary_header=False)
        self.get_resource(
            sensor.links['ch:dataHistory'].href + "&timestamp__gte=NaN",
            expect_status_code=HTTP_STATUS_BAD_REQUEST,
            check_mime_type=False,
            check_vary_header=False)
        self.get_resource(
            sensor.links['ch:dataHistory'].href + "&timestamp__lt=NaN",
            expect_status_code=HTTP_STATUS_BAD_REQUEST,
            check_mime_type=False,
            check_vary_header=False)
        self.get_resource(
            sensor.links['ch:dataHistory'].href +
            "&timestamp__lt=TestingBadInput",
            expect_status_code=HTTP_STATUS_BAD_REQUEST,
            check_mime_type=False,
            check_vary_header=False)


class ApiAggregateScalarSensorDataTests(ChainTestCase):

    def setUp(self):
        super(ApiAggregateScalarSensorDataTests, self).setUp()
        time_end = now() + timedelta(days=1)
        time_begin = time_end - timedelta(days=7)
        time_end = time_end.strftime("%Y-%m-%d")
        time_begin = time_begin.strftime("%Y-%m-%d")
        resources.influx_client.post('query', '''
            SELECT max("value"), min("value"), mean("value"), count("value"), sum("value")
            INTO "{0}" FROM "{1}" WHERE "time" < '{2}' AND "time" >= '{3}'
            GROUP BY "sensor_id", time(1h), *'''.format(INFLUX_MEASUREMENT + '_1h',
                                                        INFLUX_MEASUREMENT,
                                                        time_end,
                                                        time_begin), True)
        resources.influx_client.post('query', '''
            SELECT max("max"), min("min"), sum("sum")/sum("count") as "mean", sum("count") as "count", sum("sum")
            INTO "{0}" FROM "{1}" WHERE "time" < '{2}' AND "time" >= '{3}'
            GROUP BY "sensor_id", time(1d), *'''.format(INFLUX_MEASUREMENT + '_1d',
                                                        INFLUX_MEASUREMENT + '_1h',
                                                        time_end,
                                                        time_begin), True)
        resources.influx_client.post('query', '''
            SELECT max("max"), min("min"), sum("sum")/sum("count") as "mean", sum("count") as "count", sum("sum")
            INTO "{0}" FROM "{1}" WHERE "time" < '{2}' AND "time" >= '{3}'
            GROUP BY "sensor_id", time(1w), *'''.format(INFLUX_MEASUREMENT + '_1w',
                                                        INFLUX_MEASUREMENT + '_1d',
                                                        time_end,
                                                        time_begin), True)

    def test_aggregate_sensor_data_query_should_include_argument(self):
        sensor = self.get_a_sensor()
        self.get_resource(
            sensor.links['ch:aggregateData'].href,
            expect_status_code=HTTP_STATUS_BAD_REQUEST,
            check_mime_type=False,
            check_vary_header=False)

    def test_aggregate_sensor_data_should_have_data_type(self):
        sensor = self.get_a_sensor()
        sensor_data = self.get_resource(
            sensor.links['ch:aggregateData'].href.replace('{&aggtime}', '&aggtime=1h'))
        self.assertIn('dataType', sensor_data)
        self.assertEqual('float', sensor_data.dataType)

    def test_aggregate_sensor_data_should_have_timestamp_and_statistics(self):
        sensor = self.get_a_sensor()
        href = sensor.links['ch:aggregateData'].href
        params = ['1h', '1d', '1w']
        time_end = now() + timedelta(days=1)
        time_begin = time_end - timedelta(days=9)
        time_end = time.mktime(time_end.timetuple())
        time_begin = time.mktime(time_begin.timetuple())
        for param in params:
            # make sure there is data
            sensor_data = self.get_resource(
                href.replace('{&aggtime}', '&aggtime=' + param) +
                '&timestamp__gte={0}&timestamp__lt={1}'.format(time_begin, time_end))
            self.assertIn('timestamp', sensor_data.data[0])
            self.assertIn('min', sensor_data.data[0])
            self.assertIn('max', sensor_data.data[0])
            self.assertIn('mean', sensor_data.data[0])
            self.assertIn('count', sensor_data.data[0])

    def test_aggregate_sensor_data_invalid_arguments(self):
        sensor = self.get_a_sensor()
        href = sensor.links['ch:aggregateData'].href
        self.get_resource(
            href.replace('{&aggtime}', '&aggtime=1s'),
            expect_status_code=HTTP_STATUS_BAD_REQUEST,
            check_mime_type=False,
            check_vary_header=False)

class ApiMetadataTests(ChainTestCase):

    def test_site_device_sensor_should_have_metadata_link(self):
        resources = self.get_site_device_sensor()
        for resource in resources:
            self.assertIn('ch:metadata', resource.links)
            self.assertEqual('Metadata', resource.links['ch:metadata'].title)

    def test_metadata_should_be_postable_to_site_device_resource(self):
        resources = self.get_site_device_sensor()
        for resource in resources:
            metadata = self.get_resource(resource.links['ch:metadata'].href)
            metadata_url = metadata.links.createForm.href
            new_metadata = {
                "key": "Test",
                "value": "Unit Test Metadata",
                "timestamp": now().isoformat()
            }
            self.create_resource(metadata_url, new_metadata)
            # ids contain object_id and type_id
            ids = re.findall('\w+=(\d+)', metadata_url)
            db_metadata = Metadata.objects.get(content_type__pk=ids[0], object_id=ids[1], value=new_metadata['value'])
            self.assertEqual(db_metadata.value, new_metadata['value'])
            self.assertEqual(db_metadata.key, new_metadata['key'])

    def test_posting_metadata_should_sanitize_args_for_response(self):
        resources = self.get_site_device_sensor()
        for resource in resources:
            metadata = self.get_resource(resource.links['ch:metadata'].href)
            metadata_url = metadata.links.createForm.href
            new_metadata = {
                "key": "Test",
                "value": 123,
                "timestamp": now().isoformat()
            }
            response = self.create_resource(metadata_url, new_metadata)
            self.assertEqual(type(response.value), unicode)
            self.assertEqual(response.value, '123')

    def test_metadata_query_should_return_most_recent_key_value(self):
        device = self.get_a_device()
        metadata = self.get_resource(device.links['ch:metadata'].href)
        metadata_url = metadata.links.createForm.href
        new_time = now()
        old_time = new_time - timedelta(minutes=2)
        new_metadata = [
            {
                "key": "Test",
                "value": "Old Metadata",
                "timestamp": old_time.isoformat()
            },
            {
                "key": "Test",
                "value": "New Metadata",
                "timestamp": new_time.isoformat()
            }
        ]
        self.create_resource(metadata_url, new_metadata)
        # query again
        metadata = self.get_resource(device.links['ch:metadata'].href)
        self.assertIn('data', metadata)
        self.assertEqual(type(metadata.data), list)
        self.assertGreater(len(metadata.data), 0)
        data_found = False
        for data in metadata.data:
            if data['key'] == 'Test':
                self.assertEqual(data['value'], 'New Metadata')
                data_found = True
        self.assertTrue(data_found)

    def test_metadata_should_be_immutable(self):
        device = self.get_a_device()
        metadata = self.get_resource(device.links['ch:metadata'].href)
        metadata_url = metadata.links.createForm.href
        new_metadata = {
            "key": "Test edit",
            "value": 123,
            "timestamp": now().isoformat()
        }
        response = self.create_resource(metadata_url, new_metadata)
        metadata_id = re.search(r'(\d+)$', response.links.self.href).group(0)
        edit_url = BASE_API_URL + 'metadata/' + metadata_id + '/edit'
        mime_type = 'application/hal+json'
        accept_header = mime_type + ',' + ACCEPT_TAIL
        response = None
        try:
            response = self.client.post(edit_url,
                                        new_metadata,
                                        content_type=mime_type,
                                        HTTP_ACCEPT=accept_header,
                                        HTTP_HOST='localhost')
        except:
            self.assertTrue(False)
        self.assertEqual(response.status_code, HTTP_STATUS_BAD_REQUEST)
        self.assertEqual(response['Content-Type'], "application/json")


# these tests are testing specific URL conventions within this application
class CollectionFilteringTests(ChainTestCase):

    def test_devices_can_be_filtered_by_site(self):
        full_devices_coll = self.get_resource(BASE_API_URL + 'devices/')
        filtered_devices_coll = self.get_resource(
            BASE_API_URL + 'devices/?site=%d' % self.sites[0].id)
        self.assertEqual(len(full_devices_coll.links.items), len(self.devices))
        self.assertEqual(len(filtered_devices_coll.links.items),
                         len([d for d in self.devices
                              if d.site==self.sites[0]]))

    def test_filtered_collection_has_filtered_url(self):
        site_id = self.sites[0].id
        coll = self.get_resource(
            BASE_API_URL + 'devices/?site=%d' % site_id)
        self.assertTrue(('site=%d' % site_id) in coll.links.self.href)

    def test_device_collections_should_limit_to_default_page_size(self):
        site = self.get_a_site()
        devices = self.get_resource(site.links['ch:devices'].href)
        create_url = devices.links['createForm'].href
        # make sure we create more devices than will fit on a page
        for i in range(0, DeviceResource.page_size + 1):
            dev = {'name': 'test dev %d' % i}
            self.create_resource(create_url, dev)
        devs = self.get_resource(BASE_API_URL + 'devices/')
        self.assertEqual(len(devs.links.items), DeviceResource.page_size)

    def test_pages_should_have_next_and_prev_links(self):
        site = self.get_a_site()
        devices = self.get_resource(site.links['ch:devices'].href)
        create_url = devices.links['createForm'].href
        # make sure we create more devices than will fit on a page
        for i in range(0, DeviceResource.page_size + 1):
            dev = {'name': 'test dev %d' % i}
            self.create_resource(create_url, dev)
        devs = self.get_resource(site.links['ch:devices'].href)
        self.assertIn('next', devs.links)
        self.assertNotIn('previous', devs.links)
        next_devs = self.get_resource(devs.links.next.href)
        self.assertIn('previous', next_devs.links)
        self.assertNotIn('next', next_devs.links)


class HTMLTests(ChainTestCase):

    def test_root_request_accepting_html_gets_it(self):
        res = self.get_resource(BASE_API_URL, mime_type='text/html').strip()
        # check that it startswith a doctype
        self.assertTrue(res.startswith("<!DOCTYPE html"))
        self.assertTrue(res.endswith("</html>"))


class ErrorTests(TestCase):

    def test_unsupported_mime_types_should_return_406_status(self):
        response = self.client.get(BASE_API_URL, HTTP_ACCEPT='foobar')
        self.assertEqual(response.status_code, HTTP_STATUS_NOT_ACCEPTABLE)
        self.assertEqual(response['Content-Type'], 'application/hal+json')
        self.assertIn('message', json.loads(response.content))

    def test_if_client_accepts_wildcard_send_hal_json(self):
        response = self.client.get(BASE_API_URL, HTTP_ACCEPT='foobar, */*')
        self.assertEqual(response.status_code, HTTP_STATUS_SUCCESS)
        self.assertEqual(response['Content-Type'], 'application/hal+json')

    def test_bad_url_returns_404(self):
        response = self.client.get('/foobar/',
                                   HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, HTTP_STATUS_NOT_FOUND)
        self.assertEqual(response['Content-Type'], 'application/json')
        self.assertIn('message', json.loads(response.content))
