# CAFS – Community Appointment & Feedback System

A cloud-native, serverless web application deployed on AWS that enables community members to book appointments with local services and submit structured feedback.

## Architecture Overview

```
Browser → CloudFront → S3 (Static Frontend)
Browser → API Gateway → Lambda (Python 3.12) → DynamoDB
Lambda → SNS (Email notifications)
Lambda → S3 (Attachment pre-signed URLs)
CloudWatch → Alarms → SNS
```

## AWS Services Used

| Service | Role |
|---|---|
| S3 | Static frontend hosting + attachment storage |
| CloudFront | CDN, HTTPS termination, edge caching |
| API Gateway | RESTful HTTP routing, throttling, CORS |
| Lambda (Python) | Serverless CRUD business logic |
| DynamoDB | NoSQL data store with GSIs |
| SNS | Email/SMS appointment confirmations |
| IAM | Least-privilege roles and policies |
| CloudFormation | Infrastructure-as-Code (IaC) |
| CloudWatch | Metrics, alarms, Lambda logs |
| X-Ray | Distributed tracing |

## Project Structure

```
cafs/
├── lambda/
│   ├── appointments_handler.py   # Appointments CRUD Lambda
│   └── feedback_handler.py       # Feedback CRUD + S3 upload URL Lambda
├── frontend/
│   └── index.html                # Single-page application (vanilla JS)
├── infra/
│   └── cloudformation.yaml       # Full IaC stack definition
└── README.md
```

## Deployment Steps

### Prerequisites
- AWS CLI configured (`aws configure`)
- S3 bucket for Lambda deployment artefacts

### 1. Package Lambda functions
```bash
cd lambda
zip appointments_handler.zip appointments_handler.py
zip feedback_handler.zip feedback_handler.py
aws s3 cp appointments_handler.zip s3://YOUR-DEPLOY-BUCKET/lambda/
aws s3 cp feedback_handler.zip s3://YOUR-DEPLOY-BUCKET/lambda/
```

### 2. Deploy CloudFormation stack
```bash
aws cloudformation deploy \
  --template-file infra/cloudformation.yaml \
  --stack-name cafs-prod \
  --parameter-overrides \
      Environment=prod \
      LambdaCodeBucket=YOUR-DEPLOY-BUCKET \
  --capabilities CAPABILITY_NAMED_IAM
```

### 3. Deploy frontend
```bash
BUCKET=$(aws cloudformation describe-stacks \
  --stack-name cafs-prod \
  --query "Stacks[0].Outputs[?OutputKey=='FrontendBucketName'].OutputValue" \
  --output text)

aws s3 sync frontend/ s3://$BUCKET/ --delete
```

### 4. Retrieve API endpoint
```bash
aws cloudformation describe-stacks \
  --stack-name cafs-prod \
  --query "Stacks[0].Outputs[?OutputKey=='ApiEndpoint'].OutputValue" \
  --output text
```
Update `API_BASE` in `frontend/index.html` with this value.

## Security Posture

- **IAM least privilege** – Lambda role scoped to only required DynamoDB tables and S3 bucket
- **Encryption at rest** – DynamoDB SSE enabled; S3 AES-256 server-side encryption
- **Encryption in transit** – All traffic via HTTPS (CloudFront + API Gateway TLS)
- **No public S3** – Frontend served via CloudFront OAI; attachments via pre-signed URLs only
- **PITR** – 35-day point-in-time recovery on both DynamoDB tables
- **CORS** – Restricted to CloudFront domain in production (wildcard only in dev)

## Cost Estimate (Monthly, Low Traffic ~1,000 appointments/month)

| Service | Estimated Cost |
|---|---|
| Lambda (1M requests free tier) | ~$0.00 |
| DynamoDB (on-demand, <1 GB) | ~$0.25 |
| S3 (5 GB storage + requests) | ~$0.15 |
| API Gateway (1M calls) | ~$3.50 |
| CloudFront (10 GB transfer) | ~$0.85 |
| **Total** | **~$4.75/month** |

## References

- Mell, P. & Grance, T. (2011) NIST SP 800-145
- Erl, T., Mahmood, Z. & Puttini, R. (2013) *Cloud Computing: Concepts, Technology & Architecture*
- AWS Well-Architected Framework (2023)
