# GET /v1/fee-sharing/account/{accountId}/shares

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/v1/fee-sharing/account/{accountId}/shares`
- **Method:** `GET`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `getV1Fee-sharingAccountByAccountIdShares`
- **Summary:** GET /v1/fee-sharing/account/{accountId}/shares

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `accountId` | `string` | path | ✓ |  |
| `cursor` | `string` | query | ✓ |  |
| `limit` | `string` | query | ✓ |  |

## Responses

### 200

## Example Usage

### cURL

```bash
curl -X GET "https://advanced-api-v2.pump.fun/v1/fee-sharing/account/<accountId>/shares?cursor=<cursor>&limit=<limit>" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
