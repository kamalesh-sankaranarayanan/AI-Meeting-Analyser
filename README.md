# AI Meeting-to-Execution Agent (V1.0)

## Overview

An Agentic AI system that automatically converts meeting recordings into actionable tasks.

The system transcribes meeting audio using Whisper, extracts tasks, owners, and deadlines using OpenRouter LLMs, and stores the extracted information in a SQLite database for further tracking.

## Features

* Audio Transcription using Whisper
* Task Extraction using LLM
* Owner Identification
* Deadline Detection
* SQLite Database Storage

## Technology Stack

* Python
* Whisper
* OpenRouter
* SQLite
* Git & GitHub

## Current Workflow

Meeting Audio → Whisper → Transcript → OpenRouter → Tasks → SQLite

## Version

V1.0 - Core AI Pipeline
