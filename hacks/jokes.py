import random, json, os, fcntl
from flask import current_app

csp_topics_data = []

# AP CSP Topics - Big Ideas 1-5
topic_list = [
    "Big Idea 1: Creative Development - Collaboration and program development",
    "Big Idea 1: Creative Development - Design and testing",
    "Big Idea 2: Data - Binary numbers and data compression",
    "Big Idea 2: Data - Extracting information from data",
    "Big Idea 2: Data - Using programs with data",
    "Big Idea 3: Algorithms and Programming - Variables and assignments",
    "Big Idea 3: Algorithms and Programming - Data abstraction (lists)",
    "Big Idea 3: Algorithms and Programming - Mathematical expressions",
    "Big Idea 3: Algorithms and Programming - Strings",
    "Big Idea 3: Algorithms and Programming - Boolean expressions",
    "Big Idea 3: Algorithms and Programming - Conditionals (if/else)",
    "Big Idea 3: Algorithms and Programming - Loops (iteration)",
    "Big Idea 3: Algorithms and Programming - Functions and procedures",
    "Big Idea 3: Algorithms and Programming - Algorithms",
    "Big Idea 3: Algorithms and Programming - Simulations",
    "Big Idea 4: Computing Systems and Networks - Internet basics",
    "Big Idea 4: Computing Systems and Networks - Fault tolerance and redundancy",
    "Big Idea 4: Computing Systems and Networks - Parallel and distributed computing",
    "Big Idea 5: Impact of Computing - Beneficial and harmful effects",
    "Big Idea 5: Impact of Computing - Digital divide",
    "Big Idea 5: Impact of Computing - Bias in computing",
    "Big Idea 5: Impact of Computing - Legal and ethical concerns (privacy, intellectual property)",
    "Big Idea 5: Impact of Computing - Safe computing and cybersecurity"
]

def get_topics_file():
    # Use Flask app.config['DATA_FOLDER'] for shared data
    data_folder = current_app.config['DATA_FOLDER']
    return os.path.join(data_folder, 'csp_topics.json')

def _read_topics_file():
    TOPICS_FILE = get_topics_file()
    if not os.path.exists(TOPICS_FILE):
        return []
    with open(TOPICS_FILE, 'r') as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        try:
            data = json.load(f)
        except Exception:
            data = []
        fcntl.flock(f, fcntl.LOCK_UN)
    return data

def _write_topics_file(data):
    TOPICS_FILE = get_topics_file()
    with open(TOPICS_FILE, 'w') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        json.dump(data, f, indent=2)
        fcntl.flock(f, fcntl.LOCK_UN)

def initJokes():
    """Initialize topics (keeping function name for compatibility)"""
    TOPICS_FILE = get_topics_file()
    # Only initialize if file does not exist
    if os.path.exists(TOPICS_FILE):
        return
    csp_topics_data = []
    item_id = 0
    for item in topic_list:
        csp_topics_data.append({
            "id": item_id, 
            "topic": item, 
            "need_review": 0,      # Students who need review on this
            "understand_well": 0    # Students who understand this well
        })
        item_id += 1
    
    # Add some initial random votes for testing
    for i in range(15):
        id = random.choice(csp_topics_data)['id']
        csp_topics_data[id]['need_review'] += 1
    for i in range(10):
        id = random.choice(csp_topics_data)['id']
        csp_topics_data[id]['understand_well'] += 1
    
    _write_topics_file(csp_topics_data)
        
def getJokes():
    """Get all topics"""
    return _read_topics_file()

def getJoke(id):
    """Get a specific topic by id"""
    topics = _read_topics_file()
    return topics[id]

def getRandomJoke():
    """Get a random topic"""
    topics = _read_topics_file()
    return random.choice(topics)

def favoriteJoke():
    """Get the topic most students need help with"""
    topics = _read_topics_file()
    most_needed = 0
    most_needed_id = -1
    for topic in topics:
        if topic['need_review'] > most_needed:
            most_needed = topic['need_review']
            most_needed_id = topic['id']
    return topics[most_needed_id] if most_needed_id != -1 else None
    
def jeeredJoke():
    """Get the topic students understand best"""
    topics = _read_topics_file()
    best = 0
    best_id = -1
    for topic in topics:
        if topic['understand_well'] > best:
            best = topic['understand_well']
            best_id = topic['id']
    return topics[best_id] if best_id != -1 else None

# Atomic vote update with exclusive lock
def _vote_topic(id, field):
    TOPICS_FILE = get_topics_file()
    with open(TOPICS_FILE, 'r+') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        topics = json.load(f)
        topics[id][field] += 1
        f.seek(0)
        json.dump(topics, f, indent=2)
        f.truncate()
        fcntl.flock(f, fcntl.LOCK_UN)
    return topics[id][field]

def addJokeHaHa(id):
    """Increment 'need_review' count for a topic"""
    return _vote_topic(id, 'need_review')

def addJokeBooHoo(id):
    """Increment 'understand_well' count for a topic"""
    return _vote_topic(id, 'understand_well')

def printJoke(topic):
    """Print topic information"""
    print(topic['id'], topic['topic'], "\n", 
          "Need Review:", topic['need_review'], "\n", 
          "Understand Well:", topic['understand_well'], "\n")

def countJokes():
    """Count total number of topics"""
    topics = _read_topics_file()
    return len(topics)

if __name__ == "__main__": 
    initJokes()  # initialize topics
    
    # Most needed and best understood
    most_needed = favoriteJoke()
    if most_needed:
        print("Most students need review on:", most_needed['need_review'], "votes")
        printJoke(most_needed)
    
    best = jeeredJoke()
    if best:
        print("Best understood topic:", best['understand_well'], "votes")
        printJoke(best)
    
    
    # Random topic
    print("Random topic:")
    printJoke(getRandomJoke())
    
    # Count of Topics
    print("Topics Count: " + str(countJokes()))