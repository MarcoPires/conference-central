#!/usr/bin/env python

"""
conference.py -- Udacity conference server-side Python App Engine API;
	uses Google Cloud Endpoints

$Id: conference.py,v 1.25 2014/05/24 23:42:19 wesc Exp wesc $

created by wesc on 2014 apr 21

"""

__author__ = 'wesc+api@google.com (Wesley Chun)'


from datetime import datetime

# - - - Appengine - - - - - - - - - - - - - - - - - - - -
import endpoints
from protorpc import messages
from protorpc import message_types
from protorpc import remote

from google.appengine.api import memcache
from google.appengine.api import taskqueue
from google.appengine.ext import ndb

# - - - Models - - - - - - - - - - - - - - - - - - - -
from models import ConflictException
from models import Profile
from models import ProfileMiniForm
from models import ProfileForm
from models import TeeShirtSize

from models import Conference
from models import ConferenceForm
from models import ConferenceForms
from models import ConferenceQueryForm
from models import ConferenceQueryForms

from models import StringMessage
from models import BooleanMessage

from models import Session
from models import SessionType
from models import SessionForm
from models import SessionForms


# - - - Helpers - - - - - - - - - - - - - - - - - - - -
from utils import getUserId

from settings import WEB_CLIENT_ID

EMAIL_SCOPE = endpoints.EMAIL_SCOPE
API_EXPLORER_CLIENT_ID = endpoints.API_EXPLORER_CLIENT_ID
MEMCACHE_ANNOUNCEMENTS_KEY = "RECENT_ANNOUNCEMENTS"
MEMCACHE_SESS_ANNOUNCEMENTS_KEY = "SESS_RECENT_ANNOUNCEMENTS"

# - - - Constant - - - - - - - - - - - - - - - - - - - - - - - - - - -

DEFAULTS = {
	"city": "Default City",
	"maxAttendees": 0,
	"seatsAvailable": 0,
	"topics": [ "Default", "Topic" ],
}

OPERATORS = {
			'EQ':   '=',
			'GT':   '>',
			'GTEQ': '>=',
			'LT':   '<',
			'LTEQ': '<=',
			'NE':   '!='
			}

FIELDS =    {
			'CITY': 'city',
			'TOPIC': 'topics',
			'MONTH': 'month',
			'MAX_ATTENDEES': 'maxAttendees',
			}

CONF_GET_REQUEST = endpoints.ResourceContainer(
	message_types.VoidMessage,
	websafeConferenceKey=messages.StringField(1)
)

CONF_POST_REQUEST = endpoints.ResourceContainer(
	ConferenceForm,
	websafeConferenceKey=messages.StringField(1)
)

SESS_POST_REQUEST = endpoints.ResourceContainer(
	SessionForm,
	websafeConferenceKey=messages.StringField(1)
)

SESS_GET_BY_SPEAKER = endpoints.ResourceContainer(
	speaker=messages.StringField(1)
)

SESS_GET_BY_DURATION = endpoints.ResourceContainer(
	duration=messages.IntegerField(1)
)

SESS_GET_BY_TYPE_REQUEST = endpoints.ResourceContainer(
	websafeConferenceKey=messages.StringField(1),
	typeOfSession=messages.StringField(2)
)

CONF_SESS_GET_REQUEST = endpoints.ResourceContainer(
	websafeSessionKey=messages.StringField(1)
)

# - - - APP - - - - - - - - - - - - - - - - - - - - - - - - - - -
@endpoints.api(name='conference', version='v1', 
	allowed_client_ids=[WEB_CLIENT_ID, API_EXPLORER_CLIENT_ID],
	scopes=[EMAIL_SCOPE])
class ConferenceApi(remote.Service):
	"""Conference API v0.1"""

# - - - Conference objects - - - - - - - - - - - - - - - - -

	def _copyConferenceToForm(self, conf, displayName):
		"""Copy relevant fields from Conference to ConferenceForm."""
		cf = ConferenceForm()
		for field in cf.all_fields():
			if hasattr(conf, field.name):
				# convert Date to date string; just copy others
				if field.name.endswith('Date'):
					setattr(cf, field.name, str(getattr(conf, field.name)))
				else:
					setattr(cf, field.name, getattr(conf, field.name))
			elif field.name == "websafeKey":
				setattr(cf, field.name, conf.key.urlsafe())
		if displayName:
			setattr(cf, 'organizerDisplayName', displayName)
		cf.check_initialized()
		return cf


	def _createConferenceObject(self, request):
		"""Create or update Conference object, returning ConferenceForm/request."""
		# preload necessary data items
		user = endpoints.get_current_user()
		if not user:
			raise endpoints.UnauthorizedException('Authorization required')
		user_id = getUserId(user)

		if not request.name:
			raise endpoints.BadRequestException("Conference 'name' field required")

		# copy ConferenceForm/ProtoRPC Message into dict
		data = {field.name: getattr(request, field.name) for field in request.all_fields()}
		del data['websafeKey']
		del data['organizerDisplayName']

		# add default values for those missing (both data model & outbound Message)
		for df in DEFAULTS:
			if data[df] in (None, []):
				data[df] = DEFAULTS[df]
				setattr(request, df, DEFAULTS[df])

		# convert dates from strings to Date objects; set month based on start_date
		if data['startDate']:
			data['startDate'] = datetime.strptime(data['startDate'][:10], "%Y-%m-%d").date()
			data['month'] = data['startDate'].month
		else:
			data['month'] = 0
		if data['endDate']:
			data['endDate'] = datetime.strptime(data['endDate'][:10], "%Y-%m-%d").date()

		# set seatsAvailable to be same as maxAttendees on creation
		if data["maxAttendees"] > 0:
			data["seatsAvailable"] = data["maxAttendees"]
		# generate Profile Key based on user ID and Conference
		# ID based on Profile key get Conference key from ID
		p_key = ndb.Key(Profile, user_id)
		c_id = Conference.allocate_ids(size=1, parent=p_key)[0]
		c_key = ndb.Key(Conference, c_id, parent=p_key)
		data['key'] = c_key
		data['organizerUserId'] = request.organizerUserId = user_id

		# create Conference, send email to organizer confirming
		# creation of Conference & return (modified) ConferenceForm
		Conference(**data).put()
		taskqueue.add(params={'email': user.email(),
			'conferenceInfo': repr(request)},
			url='/tasks/send_confirmation_email'
		)
		return request


	@ndb.transactional()
	def _updateConferenceObject(self, request):
		user = endpoints.get_current_user()
		if not user:
			raise endpoints.UnauthorizedException('Authorization required')
		user_id = getUserId(user)

		# copy ConferenceForm/ProtoRPC Message into dict
		data = {field.name: getattr(request, field.name) for field in request.all_fields()}

		# update existing conference
		conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
		
		# check that conference exists
		if not conf:
			raise endpoints.NotFoundException(
				'No conference found with key: %s' % request.websafeConferenceKey)

		# check that user is owner
		if user_id != conf.organizerUserId:
			raise endpoints.ForbiddenException(
				'Only the owner can update the conference.')

		# Not getting all the fields, so don't create a new object; just
		# copy relevant fields from ConferenceForm to Conference object
		for field in request.all_fields():
			data = getattr(request, field.name)
			# only copy fields where we get data
			if data not in (None, []):
				# special handling for dates (convert string to Date)
				if field.name in ('startDate', 'endDate'):
					data = datetime.strptime(data, "%Y-%m-%d").date()
					if field.name == 'startDate':
						conf.month = data.month
				# write to Conference object
				setattr(conf, field.name, data)
		conf.put()
		prof = ndb.Key(Profile, user_id).get()
		return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


	@endpoints.method(ConferenceForm, ConferenceForm, path='conference',
		http_method='POST', name='createConference')
	def createConference(self, request):
		"""Create new conference."""
		return self._createConferenceObject(request)


	@endpoints.method(CONF_POST_REQUEST, ConferenceForm,
		path='conference/{websafeConferenceKey}',
		http_method='PUT', name='updateConference')
	def updateConference(self, request):
		"""Update conference w/provided fields & return w/updated info."""
		return self._updateConferenceObject(request)


	@endpoints.method(CONF_GET_REQUEST, ConferenceForm,
		path='conference/{websafeConferenceKey}',
		http_method='GET', name='getConference')
	def getConference(self, request):
		"""Return requested conference (by websafeConferenceKey)."""
		# get Conference object from request; bail if not found
		conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
		if not conf:
			raise endpoints.NotFoundException(
				'No conference found with key: %s' % request.websafeConferenceKey)
		prof = conf.key.parent().get()
		# return ConferenceForm
		return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


	@endpoints.method(message_types.VoidMessage, ConferenceForms,
		path='getConferencesCreated',
		http_method='POST', name='getConferencesCreated')
	def getConferencesCreated(self, request):
		"""Return conferences created by user."""
		# make sure user is authed
		user = endpoints.get_current_user()
		if not user:
			raise endpoints.UnauthorizedException('Authorization required')
		user_id =  getUserId(user)
		# create ancestor query for all key matches for this user
		confs = Conference.query(ancestor=ndb.Key(Profile, user_id))
		prof = ndb.Key(Profile, user_id).get()
		# return set of ConferenceForm objects per Conference
		return ConferenceForms(
			items=[self._copyConferenceToForm(conf, getattr(prof, 'displayName')) for conf in confs]
		)


	def _getQuery(self, request):
		"""Return formatted query from the submitted filters."""
		q = Conference.query()
		inequality_filter, filters = self._formatFilters(request.filters)

		# If exists, sort on inequality filter first
		if not inequality_filter:
			q = q.order(Conference.name)
		else:
			q = q.order(ndb.GenericProperty(inequality_filter))
			q = q.order(Conference.name)

		for filtr in filters:
			if filtr["field"] in ["month", "maxAttendees"]:
				filtr["value"] = int(filtr["value"])
			formatted_query = ndb.query.FilterNode(filtr["field"], filtr["operator"], filtr["value"])
			q = q.filter(formatted_query)
		return q


	def _formatFilters(self, filters):
		"""Parse, check validity and format user supplied filters."""
		formatted_filters = []
		inequality_field = None

		for f in filters:
			filtr = {field.name: getattr(f, field.name) for field in f.all_fields()}

			try:
				filtr["field"] = FIELDS[filtr["field"]]
				filtr["operator"] = OPERATORS[filtr["operator"]]
			except KeyError:
				raise endpoints.BadRequestException("Filter contains invalid field or operator.")

			# Every operation except "=" is an inequality
			if filtr["operator"] != "=":
				# check if inequality operation has been used in previous filters
				# disallow the filter if inequality was performed on a different field before
				# track the field on which the inequality operation is performed
				if inequality_field and inequality_field != filtr["field"]:
					raise endpoints.BadRequestException("Inequality filter is allowed on only one field.")
				else:
					inequality_field = filtr["field"]

			formatted_filters.append(filtr)
		return (inequality_field, formatted_filters)


	@endpoints.method(ConferenceQueryForms, ConferenceForms,
		path='queryConferences',
		http_method='POST',
		name='queryConferences')
	def queryConferences(self, request):
		"""Query for conferences."""
		conferences = self._getQuery(request)

		# need to fetch organiser displayName from profiles
		# get all keys and use get_multi for speed
		organisers = [(ndb.Key(Profile, conf.organizerUserId)) for conf in conferences]
		profiles = ndb.get_multi(organisers)

		# put display names in a dict for easier fetching
		names = {}
		for profile in profiles:
			names[profile.key.id()] = profile.displayName

		# return individual ConferenceForm object per Conference
		return ConferenceForms(
				items=[self._copyConferenceToForm(conf, names[conf.organizerUserId]) for conf in \
				conferences]
		)


# - - - Profile objects - - - - - - - - - - - - - - - - - - -

	def _copyProfileToForm(self, prof):
		"""Copy relevant fields from Profile to ProfileForm."""
		# copy relevant fields from Profile to ProfileForm
		pf = ProfileForm()
		for field in pf.all_fields():
			if hasattr(prof, field.name):
				# convert t-shirt string to Enum; just copy others
				if field.name == 'teeShirtSize':
					setattr(pf, field.name, getattr(TeeShirtSize, getattr(prof, field.name)))
				else:
					setattr(pf, field.name, getattr(prof, field.name))
		pf.check_initialized()
		return pf


	def _getProfileFromUser(self):
		"""Return user Profile from datastore, creating new one if non-existent."""
		# make sure user is authed
		user = endpoints.get_current_user()
		if not user:
			raise endpoints.UnauthorizedException('Authorization required')

		# get Profile from datastore
		user_id = getUserId(user)
		p_key = ndb.Key(Profile, user_id)
		profile = p_key.get()
		# create new Profile if not there
		if not profile:
			profile = Profile(
				key = p_key,
				displayName = user.nickname(),
				mainEmail= user.email(),
				teeShirtSize = str(TeeShirtSize.NOT_SPECIFIED),
			)
			profile.put()

		return profile      # return Profile


	def _doProfile(self, save_request=None):
		"""Get user Profile and return to user, possibly updating it first."""
		# get user Profile
		prof = self._getProfileFromUser()

		# if saveProfile(), process user-modifyable fields
		if save_request:
			for field in ('displayName', 'teeShirtSize'):
				if hasattr(save_request, field):
					val = getattr(save_request, field)
					if val:
						setattr(prof, field, str(val))
						#if field == 'teeShirtSize':
						#    setattr(prof, field, str(val).upper())
						#else:
						#    setattr(prof, field, val)
			prof.put()

		# return ProfileForm
		return self._copyProfileToForm(prof)


	@endpoints.method(message_types.VoidMessage, ProfileForm,
		path='profile', http_method='GET', name='getProfile')
	def getProfile(self, request):
		"""Return user profile."""
		return self._doProfile()


	@endpoints.method(ProfileMiniForm, ProfileForm,
		path='profile', http_method='POST', name='saveProfile')
	def saveProfile(self, request):
		"""Update & return user profile."""
		return self._doProfile(request)


# - - - Registration - - - - - - - - - - - - - - - - - - - -

	@ndb.transactional(xg=True)
	def _conferenceRegistration(self, request, reg=True):
		"""Register or unregister user for selected conference."""
		retval = None
		prof = self._getProfileFromUser() # get user Profile

		# check if conf exists given websafeConfKey
		# get conference; check that it exists
		wsck = request.websafeConferenceKey
		conf = ndb.Key(urlsafe=wsck).get()
		if not conf:
			raise endpoints.NotFoundException(
				'No conference found with key: %s' % wsck)

		# register
		if reg:
			# check if user already registered otherwise add
			if wsck in prof.conferenceKeysToAttend:
				raise ConflictException(
					"You have already registered for this conference")

			# check if seats avail
			if conf.seatsAvailable <= 0:
				raise ConflictException(
					"There are no seats available.")

			# register user, take away one seat
			prof.conferenceKeysToAttend.append(wsck)
			conf.seatsAvailable -= 1
			retval = True

		# unregister
		else:
			# check if user already registered
			if wsck in prof.conferenceKeysToAttend:

				# unregister user, add back one seat
				prof.conferenceKeysToAttend.remove(wsck)
				conf.seatsAvailable += 1
				retval = True
			else:
				retval = False

		# write things back to the datastore & return
		prof.put()
		conf.put()
		return BooleanMessage(data=retval)


	@endpoints.method(message_types.VoidMessage, ConferenceForms,
		path='conferences/attending',
		http_method='GET', name='getConferencesToAttend')
	def getConferencesToAttend(self, request):
		"""Get list of conferences that user has registered for."""
		prof = self._getProfileFromUser() # get user Profile
		conf_keys = [ndb.Key(urlsafe=wsck) for wsck in prof.conferenceKeysToAttend]
		conferences = ndb.get_multi(conf_keys)

		# get organizers
		organisers = [ndb.Key(Profile, conf.organizerUserId) for conf in conferences]
		profiles = ndb.get_multi(organisers)

		# put display names in a dict for easier fetching
		names = {}
		for profile in profiles:
			names[profile.key.id()] = profile.displayName

		# return set of ConferenceForm objects per Conference
		return ConferenceForms(items=[self._copyConferenceToForm(conf, names[conf.organizerUserId])\
		 for conf in conferences]
		)


	@endpoints.method(CONF_GET_REQUEST, BooleanMessage,
		path='conference/{websafeConferenceKey}',
		http_method='POST', name='registerForConference')
	def registerForConference(self, request):
		"""Register user for selected conference."""
		return self._conferenceRegistration(request)


	@endpoints.method(CONF_GET_REQUEST, BooleanMessage,
		path='conference/{websafeConferenceKey}',
		http_method='DELETE', name='unregisterFromConference')
	def unregisterFromConference(self, request):
		"""Unregister user for selected conference."""
		return self._conferenceRegistration(request, reg=False)


# - - - Announcements - - - - - - - - - - - - - - - - - - - -
	
	# Get announcment for feature session
	@staticmethod
	def _cacheAnnouncement():
		"""Create Announcement & assign to memcache; used by
		memcache cron job & putAnnouncement().
		"""
		confs = Conference.query(ndb.AND(
			Conference.seatsAvailable <= 5,
			Conference.seatsAvailable > 0)
		).fetch(projection=[Conference.name])

		if confs:
			# If there are almost sold out conferences,
			# format announcement and set it in memcache
			announcement = '%s %s' % (
				'Last chance to attend! The following conferences '
				'are nearly sold out:',
				', '.join(conf.name for conf in confs))
			memcache.set(MEMCACHE_ANNOUNCEMENTS_KEY, announcement)
		else:
			# If there are no sold out conferences,
			# delete the memcache announcements entry
			announcement = ""
			memcache.delete(MEMCACHE_ANNOUNCEMENTS_KEY)

		return announcement


	@endpoints.method(message_types.VoidMessage, StringMessage,
		path='conference/announcement/get',
		http_method='GET', name='getAnnouncement')
	def getAnnouncement(self, request):
		"""Return Announcement from memcache."""

		# return an existing announcement from Memcache or an empty string.
		announcement = memcache.get(MEMCACHE_ANNOUNCEMENTS_KEY) or ""
		return StringMessage(data=announcement)


# - - - Sessions - - - - - - - - - - - - - - - - - - - - - - -
	
	def _copySessionToForm(self, session):
		"""Copy relevant fields from Session to SessionForm."""
		
		sf = SessionForm()
		for field in sf.all_fields():
			if hasattr(session, field.name):
				# convert Date to date string; just copy others
				if field.name == 'date':
					setattr(sf, field.name, str(getattr(session, field.name)))
				elif field.name == 'startTime':
					if getattr(session, field.name):
						s = getattr(session, field.name).strftime("%H:%M")
						setattr(sf, field.name, s)
				elif field.name == 'typeOfSession':
					setattr(sf, field.name, getattr(SessionType, getattr(session, field.name)))
				else:
					setattr(sf, field.name, getattr(session, field.name))
			elif field.name == "websafeKey":
				setattr(sf, field.name, session.key.urlsafe())
		
		sf.check_initialized()
		return sf
	
	
	def _createSessionObject(self, request):
		"""Create session in conference founded by websafeConferenceKey."""

		# get Conference object from request; bail if not found
		conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()

		# ensure that satisfies requirements
		if not conf:
			raise endpoints.NotFoundException(
				'No conference found with key: %s' % request.websafeConferenceKey)
		if not isinstance(conf, Conference):
			raise endpoints.BadRequestException('Key must target to Conference')
		
		# preload necessary data items
		user = endpoints.get_current_user()
		if not user:
			raise endpoints.UnauthorizedException('Authorization required')

		# ensure that is open only to the organizer of the conference
		user_id = getUserId(user)
		if user_id != conf.organizerUserId:
			raise endpoints.UnauthorizedException(
				"Only creator of conference has ability to add session")
		
		# required fields
		if not request.name:
			raise endpoints.BadRequestException("Session 'name' field required")
		if not request.speaker:
			raise endpoints.BadRequestException("Session 'speaker' field required")
		
		# copy SessionForm/ProtoRPC Message into dict
		data = {field.name: getattr(request, field.name) for field in request.all_fields()}
		del data['websafeConferenceKey']
		del data['websafeKey']
		
		# convert dates from strings to Date objects; set month based on start_date
		if data['date']:
			data['date'] = datetime.strptime(data['date'][:10], "%Y-%m-%d").date()
		else:
			data['date'] = conf.startDate

		if data['startTime']:
			data['startTime'] = datetime.strptime(data['startTime'][:5], "%H:%M").time()
		
		data['typeOfSession'] = str(data['typeOfSession'])

		s_id = Session.allocate_ids(size=1, parent=conf.key)[0]
		s_key = ndb.Key(Session, s_id, parent=conf.key)
		data['key'] = s_key
		session = Session(**data)
		session.put()

		taskqueue.add(
			params={
				'speaker': data['speaker'],
				'websafeConferenceKey': request.websafeConferenceKey
			},
			url='/tasks/set_feature_speaker'
		)
		return self._copySessionToForm(session)

	
	@endpoints.method(SESS_POST_REQUEST, SessionForm,
		path='conference/{websafeConferenceKey}/session',
		http_method='POST', 
		name='createSession')
	def createSession(self, request):
		"""Create session in conference founded by websafeConferenceKey."""
		# get Conference object from request; bail if not found
		return self._createSessionObject(request)
	

	@endpoints.method(CONF_GET_REQUEST, SessionForms,
		path='conference/{websafeConferenceKey}/session',
		http_method='GET', 
		name='getConferenceSessions')
	def getConferenceSessions(self, request):
		"""Given a conference, return all sessions"""
		
		# get Conference object from request; bail if not found
		conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()

		# ensure that satisfies requirements
		if not conf:
			raise endpoints.NotFoundException(
				'No conference found with key: %s' % request.websafeConferenceKey)
		if not isinstance(conf, Conference):
			raise endpoints.BadRequestException('Key must target to Conference')

		# create ancestor query for all key matches for this user
		sessions = Session.query(ancestor=conf.key)
		return SessionForms(items=[self._copySessionToForm(session) for session in sessions])

		
	@endpoints.method(SESS_GET_BY_SPEAKER, SessionForms, 
		path='session/speaker/{speaker}', 
		http_method='GET', 
		name='getSessionsBySpeaker')
	def getSessionsBySpeaker(self, request):
		"""Given a speaker, return all sessions given by this particular speaker, across all conferences"""
		query = Session.query(Session.speaker == request.speaker)
		return SessionForms(items=[self._copySessionToForm(session) for session in query])

	
	@endpoints.method(SESS_GET_BY_TYPE_REQUEST, SessionForms,
		path='conference/{websafeConferenceKey}/session/session_type/{typeOfSession}',
		http_method='GET', 
		name='getConferenceSessionsByType')
	def getConferenceSessionsByType(self, request):
		"""Given a type Of Session, return all conference sessions of that type"""
		# get Conference object from request; bail if not found
		conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()

		# ensure that satisfies requirements
		if not conf:
			raise endpoints.NotFoundException(
				'No conference found with key: %s' % request.websafeConferenceKey)
		if not isinstance(conf, Conference):
			raise endpoints.BadRequestException('Key must target to Conference')
		
		query = Session.query(ancestor=conf.key)
		query = query.filter(Session.typeOfSession == request.typeOfSession)

		return SessionForms(items=[self._copySessionToForm(session) for session in query])


	@endpoints.method(SESS_GET_BY_DURATION, SessionForms, 
		path='session/max_duration/{duration}', 
		http_method='GET', 
		name='getSessionsByMaxDuration')
	def getSessionsByMaxDuration(self, request):
		"""Given a Duration, return all sessions under that duration, across all conferences"""
		query = Session.query(ndb.AND(Session.duration > 0, Session.duration < request.duration))
		return SessionForms(items=[self._copySessionToForm(session) for session in query])


	@endpoints.method(message_types.VoidMessage, SessionForms, 
		path='session/missinginformation', 
		http_method='GET', 
		name='getSessionsWithMissingInformation')
	def getSessionsWithMissingInformation(self, request):
		"""Return all sessions that have void fields"""
		query = Session.query(ndb.OR(Session.typeOfSession == 'NOT_SPECIFIED', 
			Session.duration == None, Session.startTime == None))

		return SessionForms(items=[self._copySessionToForm(session) for session in query])
	
	@endpoints.method(message_types.VoidMessage, SessionForms, 
		path='session/problem', 
		http_method='GET', 
		name='getSessionsProblem')
	def getSessionsProblem(self, request):
		"""Return all sessions that starts before 7 pm and they are not workshops"""
		
		time = datetime.strptime("19:00", "%H:%M").time()
		query = Session.query(Session.typeOfSession != 'WORKSHOP')
		
		manualQuery = []
		for session in query:
			if session.startTime != None and session.startTime < time:
				manualQuery.append(session)
		
		return SessionForms(items=[self._copySessionToForm(session) for session in manualQuery])

# - - - Featured Speaker - - - - - - - - - - - - - - - - - - - 
	
	# Get Featured Speaker
	@staticmethod
	def _cacheFeaturedSpeaker(speaker, websafeConferenceKey):
		"""Create featured speaker & assign to memcache"""
		
		conf = ndb.Key(urlsafe=websafeConferenceKey).get()
		if not conf:
			raise endpoints.NotFoundException(
				'No conference found with key: %s' % websafeConferenceKey)

		query = Session.query(ancestor=conf.key)
		query = query.filter(Session.speaker == speaker)
		query.fetch()
		
		count = query.count()
		if count > 1:
			features = 'Come watch the speaker %s in sessions %s' % (speaker,', '.join([session.name for session in query]))
			memcache.set(MEMCACHE_SESS_ANNOUNCEMENTS_KEY, features)

	@endpoints.method(message_types.VoidMessage, StringMessage,
		path='session/featured/get',
		http_method='GET',
		name='getFeaturedSpeaker')
	def getFeaturedSpeaker(self, request):
		"""Return Featured speaker of sessions from memcache."""
		return StringMessage(data=memcache.get(MEMCACHE_SESS_ANNOUNCEMENTS_KEY) or "")

# - - - Wishlist - - - - - - - - - - - - - - - - - - - - - - -
	@ndb.transactional
	def _sessionWishlist(self, request, add = True):
		"""Manage sessions in user's wishlist."""

		# get user Profile
		prof = self._getProfileFromUser()

		# check if session exists given SessionKey
		# get session; check that it exists
		websafeSessionKey = request.websafeSessionKey
		session = ndb.Key(urlsafe=websafeSessionKey).get()

		# ensure that satisfies requirements
		if not session:
			raise endpoints.NotFoundException(
				'No session found with key: %s' % websafeSessionKey)
		if not isinstance(session, Session):
			raise endpoints.BadRequestException('Key must target to Session')

		# adds the session to the users list of sessions they are interested in attending
		if add:
			if websafeSessionKey in prof.sessionKeysInWishlist:
				raise ConflictException('You have already registered for this session')
			prof.sessionKeysInWishlist.append(websafeSessionKey)
		
		# removes the session from the users list of sessions they are interested in attending
		else:
			if websafeSessionKey in prof.sessionKeysInWishlist:
				prof.sessionKeysInWishlist.remove(websafeSessionKey)

		# write things back to the datastore & return
		prof.put()
		return self._getProfWishList(request, prof)


	def _getProfWishList(self, request, prof = None):
		
		prof = prof or self._getProfileFromUser()

		# get profile wishList
		sess_keys = [ndb.Key(urlsafe=websafeSessionKey) for websafeSessionKey in prof.sessionKeysInWishlist]
		# get each session per key
		sessions = ndb.get_multi(sess_keys)

		# return set of SessionForms objects per Session
		return SessionForms(items=[self._copySessionToForm(session) for session in sessions])


	@endpoints.method(CONF_SESS_GET_REQUEST, SessionForms,
		path='session/{websafeSessionKey}',
		http_method='POST', 
		name='addSessionToWishlist')
	def addSessionToWishlist(self, request):
		"""Add session to user wishlist."""
		return self._sessionWishlist(request)


	@endpoints.method(message_types.VoidMessage, SessionForms,
		path='wishlist',
		http_method='GET',
		name='getSessionsInWishlist')
	def getSessionsInWishlist(self, request):
		"""Get list of sessions that user has added in wishlist."""
		return self._getProfWishList(request)


	@endpoints.method(CONF_SESS_GET_REQUEST, SessionForms,
		path='session/{websafeSessionKey}',
		http_method='DELETE', name='deleteSessionInWishlist')
	def deleteSessionInWishlist(self, request):
		"""Delete session to user wishlist."""
		return self._sessionWishlist(request, False)


api = endpoints.api_server([ConferenceApi]) # register API