FROM golang:1.27.1 AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY *.go ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /proxy .

# Static binary, no shell, runs as a non-root user.
FROM gcr.io/distroless/static:nonroot
COPY --from=build /proxy /proxy
USER 65532:65532
ENTRYPOINT ["/proxy"]
