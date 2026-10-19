import streamlit as st
import bcrypt
import re
from pymongo import MongoClient #for sql purposes
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

if "sql_client" not in st.session_state:
    st.session_state.sql_client = MongoClient(os.environ['PYMONGO_CONNECTION_STRING'])

chatbot_db = st.session_state.sql_client['chatbot']  # Use the chatbot database
users_collection = chatbot_db['users'] 

# Session state management
if "auth_page" not in st.session_state:
    st.session_state.auth_page = "landing"  # New state variable for tracking authentication pages

# Utility functions
def hash_password(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())

def verify_password(stored_hash, password):
    return bcrypt.checkpw(password.encode('utf-8'), stored_hash)

def validate_email(email):
    regex = r'^[a-z0-9]+[\._]?[a-z0-9]+[@]\w+[.]\w{2,3}$'
    return re.match(regex, email)

# Authentication pages
def signup_page():
    st.title("Create New Account")
    
    if not st.session_state.authenticated:
        with st.form("signup_form"):
            email = st.text_input("Email")
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            confirm_password = st.text_input("Confirm Password", type="password")
            
            if st.form_submit_button("Sign Up"):
                if not validate_email(email):
                    st.error("Please enter a valid email address")
                    return
                    
                if users_collection.find_one({"$or": [{"email": email}, {"username": username}]}):
                    st.error("Username or email already exists")
                    return
                    
                if password != confirm_password:
                    st.error("Passwords do not match")
                    return
                    
                hashed_pw = hash_password(password)
                user_data = {
                    "email": email,
                    "username": username,
                    "password": hashed_pw
                }
                
                try:
                    users_collection.insert_one(user_data)
                    st.success("Account created successfully! Please login.")
                    st.session_state.auth_page = "login"
                    st.rerun()
                except Exception as e:
                    st.error(f"Error creating account: {str(e)}")

        # Add back button outside the form
        if st.button("← Back to Landing Page", use_container_width=True):
            st.session_state.auth_page = "landing"
            st.rerun()

def login_page():
    st.title("Login to Your Account")
    
    if not st.session_state.authenticated:
        with st.form("login_form"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            
            if st.form_submit_button("Login"):
                user = users_collection.find_one({"username": username})
                
                if not user:
                    st.error("Username not found")
                    return
                    
                if verify_password(user["password"], password):
                    st.session_state.authenticated = True
                    st.session_state.username = username
                    st.rerun()
                else:
                    st.error("Incorrect password")

        # Add back button outside the form
        if st.button("← Back to Landing Page", use_container_width=True):
            st.session_state.auth_page = "landing"
            st.rerun()

def landing_page():
   
    # Create full-height container with vertical centering
    st.markdown("""
        <style>
            .main .block-container {
                padding-top: 20vh;
                padding-bottom: 20vh;
                max-width: 1000px;
            }
        </style>
    """, unsafe_allow_html=True)
    
    # Create a centered container with some styling
    col1, col2, col3 = st.columns([1, 6, 1])
   
    with col2:
        st.markdown("""
        <div style='text-align: center; padding: 20px;'>
            <h3>Get started with our versatile chat assistant</h3>
            <p>Please log in or create an account to continue</p>
        </div>
        """, unsafe_allow_html=True)
       
        # Create two buttons side by side
        col_btn1, col_btn2 = st.columns(2)
       
        with col_btn1:
            if st.button("Login", use_container_width=True):
                st.session_state.auth_page = "login"
                st.rerun()
       
        with col_btn2:
            if st.button("Sign Up", use_container_width=True):
                st.session_state.auth_page = "signup"
                st.rerun()
       
        # Optional demo or guest access
        st.markdown("---")
        if st.button("Continue as Guest", use_container_width=True, disabled=True):
            st.session_state.authenticated = True
            st.session_state.username = "Guest"
            st.rerun()

# Main Authentication Router
def auth_router():

    if st.session_state.authenticated:
        # User is already authenticated, no need to show auth pages
        return True
    
    # Route to the appropriate auth page based on state
    if st.session_state.auth_page == "landing":
        landing_page()
    elif st.session_state.auth_page == "login":
        login_page()
    elif st.session_state.auth_page == "signup":
        signup_page()
    
    # Return False to indicate user is not authenticated yet
    return False

st.set_page_config(page_title="Login/Signup", layout="centered")

auth_router()