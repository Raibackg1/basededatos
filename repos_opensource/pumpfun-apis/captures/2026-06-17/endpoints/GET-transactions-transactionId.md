# GET /transactions/{transactionId}

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/transactions/{transactionId}`
- **Method:** `GET`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `getTransactionsByTransactionId`
- **Summary:** GET /transactions/{transactionId}

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `transactionId` | `string` | path | ✓ |  |
| `dustFilter` | `string` | query | ✓ |  |

## Responses

### 200

## Example Usage

### cURL

```bash
curl -X GET "https://advanced-api-v2.pump.fun/transactions/<transactionId>?dustFilter=<dustFilter>" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
