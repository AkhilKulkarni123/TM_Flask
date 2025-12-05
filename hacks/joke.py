from flask import Blueprint, jsonify
from flask_restful import Api, Resource
import requests
import random

from hacks.jokes import *  # This stays the same!

joke_api = Blueprint('joke_api', __name__,
                   url_prefix='/api/jokes')


api = Api(joke_api)

class JokesAPI:
    # not implemented
    class _Create(Resource):
        def post(self, joke):
            pass
            
    # getJokes() - returns all topics
    class _Read(Resource):
        def get(self):
            return jsonify(getJokes())

    # getJoke(id) - returns specific topic
    class _ReadID(Resource):
        def get(self, id):
            return jsonify(getJoke(id))

    # getRandomJoke() - returns random topic
    class _ReadRandom(Resource):
        def get(self):
            return jsonify(getRandomJoke())
    
    # countJokes() - returns count
    class _ReadCount(Resource):
        def get(self):
            count = countJokes()
            countMsg = {'count': count}
            return jsonify(countMsg)
    
    # favoriteJoke() - returns most needed topic
    class _ReadFavorite(Resource):
        def get(self):
            topic = favoriteJoke()
            return jsonify(topic) if topic else jsonify({"error": "No data"})
    
    # jeeredJoke() - returns best understood topic
    class _ReadJeered(Resource):
        def get(self):
            topic = jeeredJoke()
            return jsonify(topic) if topic else jsonify({"error": "No data"})

    # PUT method: addJokeHaHa (now votes for "need review")
    class _UpdateLike(Resource):
        def put(self, id):
            addJokeHaHa(id)
            return jsonify(getJoke(id))

    # PUT method: addJokeBooHoo (now votes for "understand well")
    class _UpdateJeer(Resource):
        def put(self, id):
            addJokeBooHoo(id)
            return jsonify(getJoke(id))

    # Building RESTapi resources/interfaces
    api.add_resource(_Create, '/create/<string:joke>', '/create/<string:joke>/')
    api.add_resource(_Read, "", '/')
    api.add_resource(_ReadID, '/<int:id>', '/<int:id>/')
    api.add_resource(_ReadRandom, '/random', '/random/')
    api.add_resource(_ReadCount, '/count', '/count/')
    api.add_resource(_ReadFavorite, '/favorite', '/favorite/')
    api.add_resource(_ReadJeered, '/jeered', '/jeered/')
    api.add_resource(_UpdateLike, '/like/<int:id>', '/like/<int:id>/')
    api.add_resource(_UpdateJeer, '/jeer/<int:id>', '/jeer/<int:id>/')

if __name__ == "__main__": 
    # server = "http://127.0.0.1:5000" # run local
    server = 'https://flask.opencodingsociety.com' # run from web
    url = server + "/api/jokes"
    responses = []

    # Get count of topics on server
    count_response = requests.get(url+"/count")
    count_json = count_response.json()
    count = count_json['count']

    # Update votes test sequence
    num = str(random.randint(0, count-1))
    responses.append(
        requests.get(url+"/"+num)  # read topic by id
    ) 
    responses.append(
        requests.put(url+"/like/"+num)  # vote "need review"
    ) 
    responses.append(
        requests.put(url+"/jeer/"+num)  # vote "understand well"
    ) 

    # Obtain a random topic
    responses.append(
        requests.get(url+"/random")
    ) 
    
    # Get most needed
    responses.append(
        requests.get(url+"/favorite")
    )

    # Cycle through responses
    for response in responses:
        print(response)
        try:
            print(response.json())
        except:
            print("unknown error")