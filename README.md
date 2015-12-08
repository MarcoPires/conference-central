# Conference central

Conference central  is an application to manage conferences, as well as users can register on their favorite conferences.

### Version
1.0.0 - 08/12/2015

### Tech

Conference central uses:

* App Engine
* Python 2.7
* JavaScript
* Html
* CSS

### Requirements

Conference central needs:

* Python 2.7
* App Engine


### Installation

1. Update the value of  application  in  app.yaml  to the app ID you have registered in the App Engine admin console.

2. Update the values at the top of  settings.py  to reflect the respective client IDs you have registered in the Developer Console.

3. Update the value of CLIENT_ID in  static/js/app.js  to the Web client ID (line 89).

4. Run the app with the Google App Engine Launcher. 

5. Ensure it's running by visiting your local server's address (by default localhost:8080).

6. Deploy your application.


Design Choices
------------

##### Task 1: Add Sessions to a Conference
* Session is defined as a pure entity with Conference entity as ancestor, with the properties: 
    * Session name 
    * highlights 
    * Speaker
    * duration 
    * typeOfSession 
    * date 
    * start time (in 24 hour notation so it can be ordered). 
* Session also contains the following Endpoints methods 
    * **getConferenceSessions(websafeConferenceKey)** - Given a conference, return all sessions
    * **getConferenceSessionsByType(websafeConferenceKey, typeOfSession)** - Given a conference, return all sessions of a specified type (eg lecture, keynote, workshop)
    * **getSessionsBySpeaker(speaker)** - Given a speaker, return all sessions given by this particular speaker, across all conferences
    * **createSession(SessionForm, websafeConferenceKey)** - open only to the organizer of the conference

##### Task 2: Add Sessions to User Wishlist
* The wishlist stores the key of sessions in the user profile. The user can only add a session to their wishlist if he register in the conference.
    * **addSessionToWishlist(SessionKey)** - adds the session to the user's list of sessions they are interested in attending You can decide if they can only add conference they have registered to attend or if the wishlist is open to all conferences.
    * **getSessionsInWishlist()** - query for all the sessions in a conference that the user is interested in
    * **deleteSessionInWishlist(SessionKey)** - removes the session from the user’s list of sessions they are interested in attending  

##### Task 3: Work on indexes and queries
* I've implemented 2 new queries
    * **getSessionsByMaxDuration(duration)** - Given a Duration, return all sessions under that duration, across all conferences
    * **getSessionsWithMissingInformation()** - Return all sessions that have void fields
* Regard to the query problem, the issue is that only one inequality filter per query is supported. My attempt at resolution is on next endpoit:
    * **getSessionsProblem()** - Return all sessions that starts before 7 pm and they are not workshops
    

##### Task 4: Add a Task
* Featured Speaker - A memcache entry is created when creating a new session and the speaker in question will have two or more sessions.


### General use and License
This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with this program.  If not, see <http://www.gnu.org/licenses/>.


### Contacts
For more information feel free to contact me:
e-mail : mpires@leya.com

